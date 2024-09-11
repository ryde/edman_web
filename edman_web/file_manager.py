import base64
import binascii
import gzip
import mimetypes
import os
from io import BytesIO
from typing import Any, List, Optional, Tuple, Union

import cv2
import gridfs
import imutils
import numpy as np
from bson import ObjectId
from edman import Config, File
from edman.exceptions import EdmanDbProcessError, EdmanInternalError
from edman.utils import Utils
from gridfs.errors import GridFSError
from PIL import Image as PILImage
from werkzeug.datastructures import FileStorage


class FileManager(File):
    def __init__(self, db=None):
        super().__init__(db)

    def web_upload(self, collection: str, oid: Union[str, ObjectId],
                   up_file: FileStorage) -> None:
        """
        ファイルアップロード処理

        :param str collection:
        :param str or ObjectId oid:
        :param FileStorage up_file:
        :return:
        """
        oid = Utils.conv_objectid(oid)

        # ドキュメント存在確認&対象ドキュメント取得
        if (doc := self.db[collection].find_one({'_id': oid})) is None:
            raise EdmanDbProcessError('対象のドキュメントが存在しません')

        try:
            # gridfsにファイルを入れる
            inserted_file_oids = self.web_grid_in(up_file)
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
                    raise EdmanDbProcessError(
                        'ドキュメントの更新ができませんでした.')
            except Exception as e:
                # 途中で例外が起きた場合、gridfsからデータを削除する
                self.fs_delete(inserted_file_oids)
                raise EdmanDbProcessError(str(e))

    def web_grid_in(self, file: FileStorage) -> list[Any]:
        """
        Gridfsへデータをアップロード

        :param FileStorage file:
        :return: inserted
        :rtype: list
        """
        inserted = []
        try:
            f = file.stream.read()
            metadata = {'filename': file.filename}
        except OSError:
            raise EdmanDbProcessError(
                'DBにファイルをアップロード出来ませんでした')
        except Exception:
            raise
        try:
            inserted.append(self.fs.put(f, **metadata))
        except GridFSError as e:
            raise EdmanDbProcessError(e)
        return inserted

    def file_download(self, oid: Union[ObjectId, str]
                      ) -> tuple[bytes, str, Optional[str]]:
        """
        GridFsからファイルをダウンロードする

        :param str or ObjectId oid:
        :rtype: tuple
        :return:
        """
        if not isinstance(oid, ObjectId):
            if ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            else:
                raise ValueError('ObjectIdに合致しません')

        # ファイル情報を取得
        try:
            content = self.fs.get(oid)
        except gridfs.errors.NoFile:
            raise ValueError('ファイルが存在しません')
        except gridfs.errors.GridFSError:
            raise

        try:
            content_data = content.read()
            # gzip圧縮されている場合は解凍する
            if binascii.hexlify(content_data[:2]) == b'1f8b':
                content_data = gzip.decompress(content_data)
        except Exception:
            raise

        file_name = content.filename
        mimetype = mimetypes.guess_type(file_name)[0]
        return content_data, file_name, mimetype

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
    def extract_thumb_list(files: list, thumbnail_suffix: list
                           ) -> List[Tuple[ObjectId, str]]:
        """
        サムネ作成対象のリストを作成する

        :param list files:
        :param list thumbnail_suffix:
        :return:
        :rtype: list
        """
        j = [os.path.splitext(i[1])[1][1:] for i in files]
        return [(files[idx][0], ext) for idx, ext in enumerate(j) if
                ext in thumbnail_suffix]

    @staticmethod
    def generate_thumbnail(content: bytes, ext: str,
                           thumbnail_size: tuple[int, int],
                           file_decode='utf-8') -> str:
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
            img.thumbnail(size=thumbnail_size, resample=PILImage.LANCZOS)
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

    @staticmethod
    def generate_thumbnail2(content: bytes, ext: str,
                            thumbnail_size: tuple[int, int],
                            file_decode='utf-8', quality=70) -> str:
        """
        サムネイル画像をbase64で作成
        ndとopen cvを利用

        :param bytes content:
        :param str ext:
        :param tuple thumbnail_size:
        :param str file_decode: default 'utf-8'
        :param int quality: default 70, jpeg quality
        :return:
        :rtype: str
        """
        try:
            arr = np.frombuffer(content, dtype=np.uint8)
            img = cv2.imdecode(arr, flags=cv2.IMREAD_COLOR)
            resize_result = imutils.resize(img, thumbnail_size[1])
            # resize_result = cv2.resize(img, thumbnail_size)
            if not ext.startswith('.'):
                ext = '.' + ext
            ret, encoded_img = cv2.imencode(
                ext,
                resize_result,
                (cv2.IMWRITE_JPEG_QUALITY, quality))

        except (IOError, KeyError) as e:
            raise EdmanInternalError(f'サムネイルが生成できませんでした {e}')
        try:
            outputfile = base64.b64encode(encoded_img).decode(file_decode)
        except Exception:
            raise
        return outputfile

    def get_thumbnails_procedure(self, files: list, thumbnail_suffix: list,
                                 thumbnail_size=(100, 100),
                                 method="pillow", quality=70) -> dict:
        """
        データをDBから出してサムネイルを取得するラッパー
        画像を文字列データとして取得

        :param list files:
        :param list thumbnail_suffix:
        :param tuple[int, int] thumbnail_size: default (100, 100)
        :param str method: default pillow, opencv(jpeg only)
        :param int quality: default 70, jpeg quality
        :return:
        :rtype: dict
        """
        thumbnails = {}
        for oid, ext in self.extract_thumb_list(files, thumbnail_suffix):
            # contentを取得
            try:
                content, _, _ = self.file_download(oid)
            except ValueError:
                raise
            try:
                if method == 'opencv':
                    # サムネイルを作成(jpgのみ)
                    image_data = self.generate_thumbnail2(content, ext,
                                                          thumbnail_size,
                                                          quality=quality)

                else:
                    # サムネイルを作成
                    image_data = self.generate_thumbnail(content, ext,
                                                         thumbnail_size)
            except Exception:
                raise
            else:
                thumbnails.update({oid: {'data': image_data, 'suffix': ext}})

        return thumbnails

    def get_images_procedure(self, files: list, suffix: list,
                             file_decode='utf-8') -> dict:
        """
        データをDBから取り出す取得するラッパー
        文字列データとして取得

        :param list files:
        :param list suffix:
        :param str file_decode: default 'utf-8'
        :return:
        :rtype: dict
        """
        result = {}
        for oid, ext in self.extract_thumb_list(files, suffix):
            # contentを取得
            try:
                content, _, _ = self.file_download(oid)
            except ValueError:
                raise
            try:
                image_data = base64.b64encode(content).decode(file_decode)
            except Exception:
                raise
            else:
                result.update({oid: {'data': image_data, 'suffix': ext}})

        return result
