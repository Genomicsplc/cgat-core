'''database.py - database utility functions
===========================================

This module contains convenience functions to work with a relational
database.


Reference
---------

'''
import time
import re
import sqlalchemy
import apsw
from pandas import DataFrame


def executewait(dbhandle, statement, regex_error="locked",
                retries=-1, wait=5):
    '''repeatedly execute an SQL statement until it succeeds.

    Arguments
    ---------
    dbhandle : object
        A DB-API conform database handle.
    statement : string
        SQL statement to execute.
    error : string
        Exception to catch and examine for error messages.
    regex_error : string
        Any error message matching `regex_error` will be ignored,
        otherwise the procedure exists.
    retries : int
        Number of retries. If set to negative number, retry indefinitely.
        If set to 0, there will be only one attempt.
    wait : int
        Number of seconds to way between retries.

    Returns
    -------
    A cursor object

    '''
    while 1:
        try:
            cc = dbhandle.execute(statement)
        except Exception as msg:
            if retries == 0:
                raise
            if not re.search("locked", str(msg)):
                raise
            time.sleep(wait)
            retries -= 1
            continue
        break
    return cc


def getColumnNames(dbhandle, table):
    """return column names of a table from a database.
    """

    cc = executewait(dbhandle, "SELECT * FROM %s LIMIT 1" % table)
    return tuple([x[0] for x in cc.description])


def getTables(dbhandle):
    """get list of tables in an sqlite database"""
    cc = executewait(
        dbhandle, """select name from sqlite_master where type='table'""")
    return tuple([x[0] for x in cc])


def toTSV(dbhandle, outfile, statement, remove_none=True):
    '''execute statement and save as tsv file
    to disk.

    If *remove_none* is true, empty/NULL values will be output as
    empty values.

    '''
    cc = dbhandle.cursor()
    cc.execute(statement)
    outfile.write("\t".join([x[0] for x in cc.description]) + "\n")

    def _str(x):
        if x is None:
            return ""
        else:
            return str(x)

    if remove_none:
        f = _str
    else:
        f = str

    outfile.write("\n".join(
        ["\t".join(map(f, x)) for x in cc]))


def db_execute(cc, statements):
    '''excute a statement or statements against a cursor'''

    if type(statements) not in (list, tuple):
        statements = [statements]

    for statement in statements:
        cc.execute(statement)


def connect(dbhandle=None, attach=None, url=None):
    """attempt to connect to database.

    If `dbhandle` is an existing connection to a database,
    it will be returned unchanged. Otherwise, this method
    will attempt to establish a connection.

    Arguments
    ---------
    url: string
        A database url
    dbhandle : object or string
        A database handle or a connection string.

    Returns
    -------
    dbhandle : object
        A DB-API2 conforming database handle
    """

    if url:
        is_sqlite3 = url.startswith("sqlite")

        if is_sqlite3:
            connect_args = {'check_same_thread': False}
        else:
            connect_args = {}

        engine = sqlalchemy.create_engine(
            url,
            connect_args=connect_args)
        return engine

    if isinstance(dbhandle, str):
        try:
            import sqlite3
        except ImportError:
            raise ValueError(
                "If an sqlite database location is passed"
                " directly the sqlite3 module must be installed")

        dbhandle = sqlite3.connect(dbhandle)

    cc = dbhandle.cursor()

    if attach is not None:
        if isinstance(attach, str):
            db_execute(cc, attach)
        elif isinstance(attach, (tuple, list)):
            for attach_statement in attach:
                db_execute(cc, attach_statement)

    return dbhandle


def execute(queries, dbhandle=None, attach=False):
    '''Execute a statement or a  list of statements (sequentially)'''

    cc = dbhandle.cursor()

    if attach:
        db_execute(cc, attach)

    db_execute(cc, queries)
    cc.close()


def fetch(query, dbhandle=None, attach=False):
    '''Fetch all query results and return'''

    cc = dbhandle.cursor()

    if attach:
        db_execute(cc, attach)

    sqlresult = cc.execute(query).fetchall()
    cc.close()
    return sqlresult


def fetch_with_names(query,
                     dbhandle=None,
                     attach=False):
    '''Fetch query results and returns them as an array of row arrays, in
       which the first entry is an array of the field names

    '''

    dbhandle = connect(dbhandle, attach=attach)

    cc = dbhandle.cursor()
    sqlresult = cc.execute(query).fetchall()

    data = []
    # http://stackoverflow.com/questions/4147707/
    # python-mysqldb-sqlite-result-as-dictionary
    field_names = [d[0] for d in cc.description]
    data.append([name for name in field_names])
    for record in sqlresult:
        line = [field for field in record]
        data.append(line)

    cc.close()
    return data


def fetch_DataFrame(query,
                    dbhandle=None,
                    attach=False):
    '''Fetch query results and returns them as a pandas dataframe'''

    dbhandle = connect(dbhandle, attach=attach)

    cc = dbhandle.cursor()
    sqlresult = cc.execute(query).fetchall()
    cc.close()

    # see http://pandas.pydata.org/pandas-docs/dev/generated/
    # pandas.DataFrame.from_records.html#pandas.DataFrame.from_records
    # this method is design to handle sql_records with proper type
    # conversion

    field_names = [d[0] for d in cc.description]
    pandas_DataFrame = DataFrame.from_records(
        sqlresult,
        columns=field_names)
    return pandas_DataFrame


def write_DataFrame(dataframe,
                    tablename,
                    dbhandle=None,
                    index=False,
                    if_exists='replace'):
    '''write a pandas dataframe to an sqlite db, index on given columns
       index columns given as a string or list eg. "gene_id" or
       ["gene_id", "start"]

    '''

    dbhandle = connect(dbhandle)

    dataframe.to_sql(tablename,
                     con=dbhandle,
                     flavor='sqlite',
                     if_exists=if_exists)

    def indexStat(tablename, column):
        istat = ('create index %(tablename)s_%(column)s '
                 'on %(tablename)s(%(column)s)') % locals()
        return istat

    if index:

        cc = dbhandle.cursor()

        if isinstance(index, str):
            istat = indexStat(tablename, index)
            print(istat)
            db_execute(cc, istat)
        elif isinstance(index, (tuple, list)):
            for column in index:
                istat = indexStat(tablename, column)
                db_execute(cc, istat)

        cc.close()


def _getfiledata(path):
    '''
    pull out the column and data information from the tsv file
    in preperation for loading to virtual table.
    '''
    columns = None
    data = []
    counter = 1
    for p in path:
        with open(p, "r") as infile:
            for line in infile:
                counter += 1

                if columns is None:
                    columns = line

                data.append(line.replace("\t", ",").strip().split(','))
    return columns, data


def apsw_connect(dbname=None, modname="tsv"):
    '''
    attempt to connect to apsw database.

    This method will attempt to establish a
    connection to a database .

    Arguments
    ---------
    modname: string
        A module name to register with sqlite
    dbname: string
        A database name to connect to

    Returns
    -------
    con : object
        A connection to a database.
    '''

    connection = apsw.Connection(dbname)

    cursor = connection.cursor()

    connection.createmodule(modname, _VirtualTable())

    return cursor


class _VirtualTable:
    '''
    Create a virtual table from  a tsv file.
    '''
    def Create(self, db, modulename, dbname, tablename, *args):
        columns, data = _getfiledata([x for x in args])
        columns = ['%s' % (x, ) for x in columns.split()]
        schema = "create table foo(" + ','.join(["'%s'" % x for x in columns]) + ")"

        return schema, _Table(columns, data)
    Connect = Create


# Represents a table
class _Table:
    def __init__(self, columns, data):
        self.columns = columns
        self.data = data

    def BestIndex(self, *args):
        return None

    def Open(self):
        return _Cursor(self)

    def Disconnect(self):
        pass

    Destroy = Disconnect


# Represents a cursor
class _Cursor:
    def __init__(self, table):
        self.table = table

    def Filter(self, *args):
        self.pos = 0

    def Eof(self):
        return self.pos >= len(self.table.data)

    def Rowid(self):
        return self.table.data[self.pos][0]

    def Column(self, col):

        return self.table.data[self.pos][col]

    def Next(self):
        self.pos += 1

    def Close(self):
        pass
