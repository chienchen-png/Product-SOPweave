from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import openpyxl
import pandas as pd


class AnalysisSourcesTests(unittest.TestCase):
    def test_failed_financial_model_is_not_marked_successful(self):
        from tools._finmod_models import run_model
        result = run_model('d1-dupont', pd.DataFrame({'irrelevant':[1]}), {})
        self.assertFalse(result['ok'])
        self.assertIn('错误', result['summary'])

    def test_analysis_budget_rejects_large_selection_without_truncation(self):
        from tools.dataframe_source import ensure_analysis_budget
        with patch('tools.dataframe_source.MAX_ANALYSIS_CELLS', 10):
            with self.assertRaisesRegex(ValueError, '数据未截断'):
                ensure_analysis_budget(6,2)

    def test_composite_join_keys_preserve_valid_multiple_department_periods(self):
        from tools.finmod_merge import merge_files_for_model
        frames = [pd.DataFrame({'月份':['一月','一月','二月','二月'],'部门':['甲','乙','甲','乙'],'收入':[10,20,30,40],'利润':[2,3,4,5],'资产':[30,40,50,60]}),pd.DataFrame({'月份':['一月','一月','二月','二月'],'部门':['甲','乙','甲','乙'],'成本':[5,6,7,8],'存货':[1,2,3,4],'应付':[3,4,5,6]})]
        with patch('tools.finmod_merge.load_files_dataframe', side_effect=frames):
            result = merge_files_for_model(None,'p',['a.xlsx','b.xlsx'])
        self.assertEqual(result['strategy'], 'join')
        self.assertEqual(result['join_keys'], ['月份','部门'])
        self.assertEqual(len(result['df']), 4)
        self.assertEqual(result['df']['收入'].sum(),100)
        self.assertEqual(result['df']['成本'].sum(),26)
    def test_modeling_loader_preserves_text_identifiers_and_matches_project_analysis(self):
        from storage.project_data_store import ProjectDataStore
        from tools.project_data_importer import ProjectDataImporter
        from tools.finmod_eval import load_dataframe
        from tools.financial_analysis_tools import _load_dataframe
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            root = Path(directory)
            db.row_factory = sqlite3.Row
            db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
            book = openpyxl.Workbook()
            for row in [['编号','金额'],['001',10],['A002',20]]:
                book.active.append(row)
            book.save(root/'data.xlsx')
            book.close()
            ProjectDataImporter(db).import_workbook('p','data.xlsx',str(root),anchor_rows={'Sheet':0})
            store = ProjectDataStore(db)
            frame = load_dataframe(store,'p','data.xlsx')
            self.assertEqual(frame['编号'].tolist(), ['001','A002'])
            with patch('tools.financial_analysis_tools._store', return_value=(store, db)):
                project_frame = _load_dataframe('data.xlsx',project_id='p')
            pd.testing.assert_frame_equal(frame,project_frame,check_dtype=False)

    def test_same_schema_files_append_records_without_losing_second_file_values(self):
        from tools.finmod_merge import merge_files_for_model
        frames = [pd.DataFrame({'月份':['一月'],'收入':[10]}),pd.DataFrame({'月份':['一月'],'收入':[20]})]
        with patch('tools.finmod_merge.load_files_dataframe', side_effect=frames):
            result = merge_files_for_model(None,'p',['a.xlsx','b.xlsx'])
        self.assertEqual(result['strategy'],'concat')
        self.assertEqual(result['df']['收入'].sum(),30)
        self.assertEqual(len(result['df']),2)

    def test_failed_selected_file_stops_analysis_instead_of_disappearing(self):
        from tools.finmod_merge import merge_files_for_model
        with patch('tools.finmod_merge.load_files_dataframe', side_effect=[pd.DataFrame({'收入':[10]}),ValueError('文件损坏')]):
            with self.assertRaisesRegex(ValueError,'b.xlsx'):
                merge_files_for_model(None,'p',['a.xlsx','b.xlsx'])

    def test_ambiguous_many_to_many_merge_is_rejected(self):
        from tools.finmod_merge import merge_files_for_model
        frames = [pd.DataFrame({'月份':['一月','一月'],'收入':[10,20]}),pd.DataFrame({'月份':['一月','一月'],'成本':[5,6]})]
        with patch('tools.finmod_merge.load_files_dataframe', side_effect=frames):
            with self.assertRaisesRegex(ValueError,'关联键'):
                merge_files_for_model(None,'p',['a.xlsx','b.xlsx'])
