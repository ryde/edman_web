import os
import base64
from pathlib import Path
import gzip
from typing import Union, List, Tuple, Any
from io import BytesIO
from werkzeug.datastructures import FileStorage
from bson import ObjectId
from PIL import Image as PILImage
from gridfs.errors import GridFSError
from edman import File, Config
from edman.utils import Utils
from edman.exceptions import EdmanDbProcessError, EdmanInternalError


class FileManager(File):
    def __init__(self, db=None):
        super().__init__(db)

    def web_upload(self, collection: str, oid: Union[str, ObjectId],
                   up_file: FileStorage, compress=False) -> None:
        """
        ファイルアップロード処理

        :param str collection:
        :param str or ObjectId oid:
        :param FileStorage up_file:
        :param bool compress:
        :return:
        """
        oid = Utils.conv_objectid(oid)

        # ドキュメント存在確認&対象ドキュメント取得
        if (doc := self.db[collection].find_one({'_id': oid})) is None:
            raise EdmanDbProcessError('対象のドキュメントが存在しません')

        try:
            # gridfsにファイルを入れる
            inserted_file_oids = self.web_grid_in(up_file, compress)
        except EdmanDbProcessError as e:
            raise e
        else:  # ドキュメントの更新
            try:
                new_doc = self.file_list_attachment(doc, inserted_file_oids)
                replace_result = self.db[collection].replace_one({'_id': oid},
                                                                 new_doc)
                if replace_result.modified_count != 1:
                    # ドキュメントが更新されていない場合はgridfsからデータを削除する
                    self.fs_delete(inserted_file_oids)
                    raise EdmanDbProcessError('ドキュメントの更新ができませんでした.')
            except Exception as e:
                # 途中で例外が起きた場合、gridfsからデータを削除する
                self.fs_delete(inserted_file_oids)
                raise EdmanDbProcessError(str(e))

    def web_grid_in(self, file: FileStorage, compress: bool) -> list[Any]:
        """
        Gridfsへデータをアップロードし
        compressに圧縮指定があればgzipで圧縮する

        :param FileStorage file:
        :param bool compress:
        :return: inserted
        :rtype: list
        """
        inserted = []

        try:
            f = file.stream.read()
            if compress:
                f = gzip.compress(f, compresslevel=self.comp_level)
                compress_type = 'gzip'
            else:
                compress_type = None

            metadata = {'filename': file.filename, 'compress': compress_type}

        except OSError:
            raise EdmanDbProcessError('DBにファイルをアップロード出来ませんでした')
        except Exception:
            raise

        try:
            inserted.append(self.fs.put(f, **metadata))
        except GridFSError as e:
            raise EdmanDbProcessError(e)
        return inserted

    def file_delete(self, collection: str, oid: Union[str, ObjectId],
                    delete_list: List[str]):
        """
        edmanからファイルを削除する

        :param str collection:
        :param str or ObjectId oid:
        :param list delete_list:
        :return:
        """
        oid = Utils.conv_objectid(oid)

        # ドキュメント存在確認&対象ドキュメント取得
        if (doc := self.db[collection].find_one({'_id': oid})) is None:
            raise EdmanDbProcessError('対象のドキュメントが存在しません')
        if not delete_list:
            raise EdmanInternalError('削除対象リストが存在しません')

        delete_items = tuple((map(lambda x: ObjectId(x), delete_list)))

        # ファイルリファレンスから指定のoidを削除する
        diff = list(set(doc[Config.file]) - set(delete_items))
        try:
            replace_doc = self.file_list_replace(doc, diff)
            replace_result = self.db[collection].replace_one(
                {'_id': oid}, replace_doc)
            if replace_result.modified_count != 1:
                # ドキュメントが更新されていない場合
                raise EdmanDbProcessError(
                    f'ファイルリファレンスを削除できませんでした.{diff} ファイルは削除されません')
        except Exception:
            raise
        else:
            # ファイルリファレンスの削除が成功した場合のみ、gridfsからデータを削除する
            try:
                self.fs_delete(list(delete_items))
            except Exception:
                raise

    @staticmethod
    def extract_thumb_list(files: list, thumbnail_suffix: list) -> List[
        Tuple[ObjectId, str]]:
        """
        サムネ作成対象のリストを作成する

        :param list files:
        :param list thumbnail_suffix:
        :return:
        :rtype: list
        """
        l = [os.path.splitext(i[1])[1][1:] for i in files]
        return [(files[idx][0], ext) for idx, ext in enumerate(l) if
                ext in thumbnail_suffix]

    @staticmethod
    def generate_thumbnail(content: bytes, ext: str,
                           thumbnail_size: tuple, file_decode='utf-8') -> str:
        """
        サムネイル画像をbase64で作成
    
        :param bytes content:
        :param str ext:
        :param tuple thumbnail_size:
        :param str file_decode: default 'utf-8'
        :return:
        :rtype: str
        """
        try:
            img = PILImage.open(BytesIO(content))
            img.thumbnail(thumbnail_size, PILImage.LANCZOS)
            thumbnail = BytesIO()
            # jpgという拡張子は利用できないので変換する
            img.save(thumbnail, 'jpeg' if ext == 'jpg' else ext)
        except (IOError, KeyError) as e:
            raise EdmanInternalError(f'サムネイルが生成できませんでした {e}')
        try:
            outputfile = base64.b64encode(thumbnail.getvalue()).decode(
                file_decode)
        except Exception:
            raise
        return outputfile
