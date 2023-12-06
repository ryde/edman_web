import configparser
# from logging import getLogger,  FileHandler, ERROR
from logging import ERROR, StreamHandler, getLogger
from pathlib import Path
from unittest import TestCase

from bson import DBRef, ObjectId
from edman import DB, Config
from pymongo import MongoClient
from pymongo import errors as py_errors

from edman_web.search_manager import SearchManager


class TestSearchManager(TestCase):
    db_server_connect = False
    test_ini: dict = {}
    client = None

    @classmethod
    def setUpClass(cls):
        # 設定読み込み
        settings = configparser.ConfigParser()
        settings.read(Path.cwd() / 'ini' / 'test_db.ini')
        cls.test_ini = dict(settings.items('DB'))
        cls.test_ini['port'] = int(cls.test_ini['port'])

        # DB作成のため、pymongoから接続
        cls.client = MongoClient(cls.test_ini['host'], cls.test_ini['port'])

        # 接続確認
        try:
            cls.client.admin.command('hello')
            cls.db_server_connect = True
            print('Use DB.')
        except py_errors.ConnectionFailure:
            print('Do not use DB.')

        if cls.db_server_connect:
            # adminで認証
            cls.client = MongoClient(
                username=cls.test_ini['admin_user'],
                password=cls.test_ini['admin_password'])
            # DB作成
            cls.client[cls.test_ini['db']].command(
                "createUser",
                cls.test_ini['user'],
                pwd=cls.test_ini['password'],
                roles=[
                    {
                        'role': 'dbOwner',
                        'db': cls.test_ini['db'],
                    },
                ],
            )
            # edmanのDB接続オブジェクト作成
            con = {
                'host': cls.test_ini['host'],
                'port': cls.test_ini['port'],
                'user': cls.test_ini['user'],
                'password': cls.test_ini['password'],
                'database': cls.test_ini['db'],
                'options': [f"authSource={cls.test_ini['db']}"]
            }
            db = DB(con)
            cls.testdb = db.get_db
            cls.search_manager = SearchManager(db)
        # else:
        #     cls.search = Search()

        cls.logger = getLogger()

        # ログを画面に出力
        ch = StreamHandler()
        ch.setLevel(ERROR)  # ハンドラーにもそれぞれログレベル、フォーマットの設定が可能
        cls.logger.addHandler(ch)  # StreamHandlerの追加

        # ログをファイルに出力
        # fh = FileHandler('./tests.log')  # 引数には出力ファイルのパスを指定
        # fh.setLevel(ERROR)  # ハンドラーには、logger以下のログレベルを設定することは出来ない(この場合、DEBUGは不可)
        # cls.logger.addHandler(fh)  # FileHandlerの追加

    @classmethod
    def tearDownClass(cls):
        if cls.db_server_connect:
            # cls.clientはpymongo経由でDB削除
            # cls.testdb.dbはedman側の接続オブジェクト経由でユーザ(自分自身)の削除
            cls.client.drop_database(cls.test_ini['db'])
            cls.testdb.command("dropUser", cls.test_ini['user'])

    def setUp(self):
        self.config = Config()
        self.parent = self.config.parent
        self.child = self.config.child
        self.date = self.config.date
        self.file = self.config.file

    def tearDown(self):
        if self.db_server_connect:
            # システムログ以外のコレクションを削除
            collections_all = self.testdb.list_collection_names()
            log_coll = 'system.profile'
            if log_coll in collections_all:
                collections_all.remove(log_coll)
            for collection in collections_all:
                self.testdb.drop_collection(collection)

    def test_get_documents(self):

        # テストデータ入力
        if not self.db_server_connect:
            return
        # docをDBに入れる
        parent_id = ObjectId()
        doc_id = ObjectId()
        child_id = ObjectId()
        parent_col = 'parent_col'
        doc_col = 'doc_col'
        child_col = 'child_col'
        insert_docs = [
            {
                'col': parent_col,
                'doc': {
                    '_id': parent_id,
                    'name': 'parent',
                    Config.child: [DBRef(doc_col, doc_id)]
                },
            },
            {
                'col': doc_col,
                'doc': {
                    '_id': doc_id,
                    'name': 'doc',
                    Config.parent: DBRef(parent_col, parent_id),
                    Config.child: [DBRef(child_col, child_id)]
                }
            },
            {
                'col': child_col,
                'doc': {
                    '_id': child_id,
                    'name': 'child',
                    Config.parent: DBRef(doc_col, doc_id),
                }
            }]
        result = {}
        for i in insert_docs:
            insert_result = self.testdb[i['col']].insert_one(i['doc'])
            result.update({i['col']: insert_result.inserted_id})

        all_docs = self.search_manager.get_documents(2, doc_col, doc_id,
                                                     parent_depth=0,
                                                     child_depth=0)
        expected = {
            parent_col: {
                'name': 'parent',
                doc_col: [{
                    'name': 'doc',
                    child_col: [{'name': 'child'}]
                }]
            }
        }
        self.assertDictEqual(expected, all_docs)
