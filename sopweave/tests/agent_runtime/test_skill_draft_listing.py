import unittest
from flask import Flask
from routes.skills import skills_bp
from tests.agent_runtime.skill_fixtures import SkillTestFixture


class SkillDraftListingTests(SkillTestFixture, unittest.TestCase):
    def test_multiple_drafts_page_without_source_text_and_exclude_published(self):
        published = self.ready(title='已发布流程')
        self.publish(published)
        one = self.library.create_draft(source=[{'kind':'text','text':'合同编号核对'}])
        two = self.ready(title='待审流程')
        app=Flask(__name__)
        app.config['SKILL_LIBRARY']=self.library
        app.register_blueprint(skills_bp)
        client=app.test_client()
        response=client.get('/api/skills/drafts?limit=1')
        self.assertEqual(response.status_code,200)
        page=response.get_json()['data']
        self.assertEqual(len(page['drafts']),1)
        self.assertTrue(page['next_cursor'])
        other=client.get('/api/skills/drafts?limit=1&cursor='+page['next_cursor']).get_json()['data']
        self.assertEqual({d['draft_id'] for d in page['drafts']+other['drafts']},{one['draft_id'],two['draft_id']})
        self.assertFalse(other['next_cursor'])
        self.assertNotIn('source',page['drafts'][0])
        self.assertNotIn('source_json',page['drafts'][0])

    def test_invalid_cursor_does_not_reset_to_first_page(self):
        app=Flask(__name__);app.config['SKILL_LIBRARY']=self.library;app.register_blueprint(skills_bp)
        self.assertEqual(app.test_client().get('/api/skills/drafts?cursor=missing').status_code,422)
