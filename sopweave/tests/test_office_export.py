import io
import tempfile
import unittest
import zipfile
import sqlite3
from contextlib import ExitStack, closing
from unittest.mock import patch
from pathlib import Path


class OfficeExportTests(unittest.TestCase):
    def test_word_export_contains_text_formula_and_image_from_chinese_space_path(self):
        from services.office_export import export_markdown_docx, find_pandoc
        if find_pandoc() is None:
            self.skipTest('Pandoc is an optional release binary')
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix='报告 导出 ') as directory:
            root = Path(directory)
            Image.new('RGB', (4, 4), 'blue').save(root / '图表 样例.png')
            (root / '矢量.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="blue"/></svg>', encoding='utf-8')
            result = export_markdown_docx('# 验收报告\n\n公式：\n\n$$x=10+20-5$$\n\n![图](<图表 样例.png>)\n\n![矢量](矢量.svg)', root)
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                xml = archive.read('word/document.xml').decode('utf-8')
                self.assertIn('验收报告', xml)
                self.assertIn('oMath', xml)
                self.assertGreaterEqual(sum(name.startswith('word/media/') for name in archive.namelist()), 2)

    def test_both_http_exports_use_the_stored_report_and_shared_converter(self):
        from flask import Flask
        from routes.apps import finmod_bp
        from routes.files import files_bp
        from storage.project_store import ProjectStore
        from storage.report_store import ReportStore
        from services.office_export import find_pandoc
        if find_pandoc() is None:
            self.skipTest('Pandoc is an optional release binary')
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            db = stack.enter_context(closing(sqlite3.connect(root / 'app.db')))
            db.row_factory = sqlite3.Row
            db.executescript(Path('storage/schema.sql').read_text(encoding='utf-8'))
            project = ProjectStore(db).create('export', str(root))
            folder = ReportStore(db).allocate(project['id'], 'report-run', root)
            report = folder / f'{folder.name}.md'
            report.write_text('# 存储报告', encoding='utf-8')
            app = Flask(__name__)
            app.register_blueprint(files_bp)
            app.register_blueprint(finmod_bp)
            with patch('routes.files.get_db', return_value=db), patch('routes.app_reports.get_db', return_value=db):
                client = app.test_client()
                for url, command in [('/api/files/export-docx', {'project_id': project['id'], 'path': report.relative_to(root).as_posix()}), ('/api/apps/finmod/export-docx', {'run_id': 'report-run'})]:
                    response = client.post(url, json=command)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                        self.assertIn('存储报告', archive.read('word/document.xml').decode('utf-8'))
