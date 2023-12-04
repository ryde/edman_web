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
            result = self.get_tree(collection_name, ObjectId(oid), exclusion)
        else:
            # 単一のドキュメント
            result = self.find(collection_name, {'_id': ObjectId(oid)},
                               parent_depth=0, child_depth=0,
                               exclusion=exclusion)
        return result
