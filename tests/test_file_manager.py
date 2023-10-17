import base64
import configparser
import mimetypes
import os
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import TestCase

import gridfs
from bson import DBRef, ObjectId
from edman import DB, Config
from PIL import Image
from pymongo import MongoClient
from pymongo import errors as py_errors
from werkzeug.datastructures import FileStorage

from edman_web.file_manager import FileManager


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
            # cls.search_manager = SearchManager(db)
            cls.file_manager = FileManager(db.get_db)
        # else:
        #     cls.search = Search()

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

    @staticmethod
    def make_txt_files(dir_path, name='file_dl_list', suffix='.txt',
                       text='test', qty=1):
        # 添付ファイル用テキストファイル作成
        p = Path(dir_path)
        for i in range(qty):
            filename = name + str(i) + suffix
            save_path = p / filename
            with save_path.open('w') as f:
                f.write(text + str(i))
        return sorted(p.glob(name + '*' + suffix))

    def test_extract_thumb_list(self):
        if not self.db_server_connect:
            return

        # 正常系
        t1 = ObjectId()
        t2 = ObjectId()
        t3 = ObjectId()
        d = {t1: 'abc.jpg', t2: 'cd.cbf', t3: 'sta.png'}
        files = list(d.items())
        thumbnail_suffix = ['jpg', 'jpeg', 'gif', 'png']
        actual = self.file_manager.extract_thumb_list(files, thumbnail_suffix)
        expected = [(t1, 'jpg'), (t3, 'png')]
        self.assertEqual(expected, actual)

        # ファイルがない場合
        d = {}
        files = list(d.items())
        actual = self.file_manager.extract_thumb_list(files, thumbnail_suffix)
        expected = []
        self.assertEqual(expected, actual)

    def test_generate_thumbnail(self):
        if not self.db_server_connect:
            return

        # 正常系
        img_size = (100, 100)
        content = Image.new("L", (200, 200))
        ext = 'png'
        img = BytesIO()
        content.save(img, ext)
        result = self.file_manager.generate_thumbnail(img.getvalue(), ext,
                                                      thumbnail_size=img_size)
        thumb_raw = Image.open(BytesIO(base64.b64decode(result)))
        actual = thumb_raw.size
        expected = img_size
        self.assertTupleEqual(expected, actual)

    def test_file_delete(self):
        if not self.db_server_connect:
            return

        with tempfile.TemporaryDirectory() as tmp_dl_dir:

            # 全消しの場合
            # (添付ファイルが単数なので_ed_attachmentキーが消える)

            # ファイル読み込み、ファイルをgridfsに入れる
            p = Path(tmp_dl_dir)
            files_oid = []
            self.fs = gridfs.GridFS(self.testdb)
            for filename in self.make_txt_files(p, 'file_dl_list'):
                with filename.open('rb') as f:
                    files_oid.append(
                        self.fs.put(f.read(), filename=filename.name))

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
                        Config.file: files_oid,
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

            self.file_manager.file_delete(doc_col, doc_id, files_oid)

            # doc_idのドキュメントにfiles_oidのoidが存在しないこと
            target_doc = self.testdb[doc_col].find_one({'_id': doc_id})

            # 全消しなので_ed_attachmentキーは存在しない
            self.assertNotIn(Config.file, target_doc)

            # 複数ファイル中の削除

            # ファイル読み込み、ファイルをgridfsに入れる
            files_oid = []
            p = Path(tmp_dl_dir)
            self.fs = gridfs.GridFS(self.testdb)
            for filename in self.make_txt_files(p, 'file_dl_list', qty=2):
                with filename.open('rb') as f:
                    files_oid.append(
                        self.fs.put(f.read(), filename=filename.name))

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
                        Config.file: files_oid,
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

            expected = [files_oid.pop(0)]
            self.file_manager.file_delete(doc_col, doc_id, files_oid)
            target_doc = self.testdb[doc_col].find_one({'_id': doc_id})
            actual = target_doc[Config.file]
            self.assertListEqual(expected, actual)

    def test_web_upload(self):
        if not self.db_server_connect:
            return

        with tempfile.TemporaryDirectory() as tmp_dl_dir:
            p = Path(tmp_dl_dir)
            for filename in self.make_txt_files(p, 'file_dl_list'):
                with filename.open('rb') as f:
                    content = f.read()
                    content_name = os.path.basename(f.name)

            # ドキュメントのインサート
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
            for i in insert_docs:
                _ = self.testdb[i['col']].insert_one(i['doc'])

            st = FileStorage(stream=BytesIO(content), filename=content_name)
            # t = st.stream.read()
            # self.assertEquals(t, content)

            # 実行
            self.file_manager.web_upload(doc_col, doc_id, st)

            # アップロードしたデータを取得する
            d = self.testdb[doc_col].find_one({'_id': doc_id})
            upload_file_oid = d[Config.file]
            fs = gridfs.GridFS(self.testdb)
            a = fs.get(upload_file_oid[0])

            # テスト
            actual = a.read()
            expected = content
            self.assertEqual(expected, actual)

    def test_web_grid_in(self):

        if not self.db_server_connect:
            return

        # 正常系
        with tempfile.TemporaryDirectory() as tmp_dl_dir:
            files = self.make_txt_files(tmp_dl_dir, 'web_grid_in', qty=2)

            # ファイルが同一か否か
            with files[0].open('rb') as f:
                content = f.read()
                st = FileStorage(stream=BytesIO(content),
                                 filename=f.name)
            self.fs = gridfs.GridFS(self.testdb)
            inserted = self.file_manager.web_grid_in(st, compress=False)
            a = self.fs.get(inserted[0])
            actual = a.read().decode()
            expected = content.decode()
            self.assertEqual(expected, actual)

            # 圧縮が効いているか否か
            # with files[1].open('rb') as f:
            #     content = f.read()
            #     st = FileStorage(stream=BytesIO(content),
            #                      filename=f.name)
            # inserted = self.file_manager.web_grid_in(st, compress=True)
            # b = self.fs.get(inserted[0])
            # if b.compress == 'gzip':
            #     actual = gzip.decompress(b.read()).decode()
            # else:  # 現状compress指定は"gzip"かNoneのみ
            #     actual = None
            # self.assertIsNotNone(b.compress)
            # expected = content.decode()
            # self.assertEqual(expected, actual)

    def test_file_download(self):

        if not self.db_server_connect:
            return

        # gridfsにファイルを入れる
        with tempfile.TemporaryDirectory() as tmp_dl_dir:
            # ファイルを作成
            files = self.make_txt_files(tmp_dl_dir, 'file_download', qty=2)
            # ファイルをgridfsに入れる
            inserted_oids = []
            self.fs = gridfs.GridFS(self.testdb)
            expected = []
            for file in files:
                with file.open('rb') as f:
                    content = f.read()
                    metadata = {'filename': os.path.basename(f.name)}
                    inserted_oids.append(self.fs.put(content, **metadata))
                    # 比較用データ作成
                    expected.append(
                        [os.path.basename(f.name), content.decode(),
                         mimetypes.guess_type(f.name)[0]])

            actual = []
            # データをダウンロード
            for oid in inserted_oids:
                content, file_name, mimetype = self.file_manager.file_download(
                    oid)
                actual.append([file_name, content.read().decode(), mimetype])
                # print(file_name, mimetype, content.read().decode())

            # テスト
            self.assertListEqual(expected, actual)
            # print(expected)
            # print(actual)

    # def test__get_thumbnails_procedure(self):
    #     # ラッパーなのでテストは割愛
    #     pass
