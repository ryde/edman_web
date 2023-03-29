from typing import Union
from bson import ObjectId
from edman import Search
from edman.json_manager import GetJsonStructure

class SearchManager(Search):
    def __init__(self, db=None):
        super().__init__(db)

    def get_documents(self, dl_select: int, collection_name: str,
                      oid: Union[ObjectId, str], parent_depth: int,
                      child_depth: int, exclusion=None) -> dict:
        """
        指定したドキュメントをDBから取得する
        :param int dl_select:
        :param str collection_name:
        :param ObjectId or str oid:
        :param int parent_depth:
        :param int child_depth:
        :param List or None exclusion:
        :return: result
        :rtype: dict
        """
        if not isinstance(oid, ObjectId):
            if ObjectId.is_valid(oid):
                oid = ObjectId(oid)
            else:
                raise ValueError('ObjectIdに合致しません')

        # 階層指定
        if dl_select == GetJsonStructure.manual_select.value:
            result = self.find(collection_name, {'_id': ObjectId(oid)},
                               parent_depth=parent_depth,
                               child_depth=child_depth, exclusion=exclusion)

        # 自分が所属するツリー全て
        elif dl_select == GetJsonStructure.all_doc.value:
            doc = self.db.doc(collection_name, ObjectId(oid), query=None,
                              reference_delete=False)

            # 自分の所属するツリーのroot情報を取得
            if (
                    root_dbref := self.db.get_root_dbref(
                        doc)) is None:  # 自分自身がrootの場合
                root_collection = collection_name
                root_doc = doc
            else:
                root_collection = root_dbref.collection
                root_doc = self.connected_db[root_collection].find_one({
                    '_id': root_dbref.id
                })

            # 自分と子要素をマージ
            root_doc.update(
                self.db.get_child_all({root_collection: root_doc}))

            # リファレンス削除や日時変換
            result = self.process_data_derived_from_mongodb(
                {root_collection: root_doc}, exclusion=exclusion)

        else:
            # 単一のドキュメント
            result = self.find(collection_name, {'_id': ObjectId(oid)},
                               parent_depth=0, child_depth=0,
                               exclusion=exclusion)
        return result
