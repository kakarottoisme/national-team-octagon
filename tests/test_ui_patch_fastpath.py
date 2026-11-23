"""Read-only UI fast-path routing and deployment ancestry tests."""
import unittest
import check_ui_patch as q

class QuickPath(unittest.TestCase):
    def test_no_data_backend_or_workflow_changes_in_fast_path(self):
        for path in ['data/live.json','data/holdings.json','.github/workflows/live.yml','ic_strategy.py','preview-tools/collect_preview.py','futures_close_update.py']:
            self.assertFalse(q.allowed(path),path)
        for path in ['index.html','dashboard.html','dashboard-shell.css','tests/test_chart_readout.py','docs/DESIGN.md','AGENTS.md']:
            self.assertTrue(q.allowed(path),path)

    def test_related_tests_only_for_page_change(self):
        selected=q.select_tests(['index.html'])
        self.assertIn('tests.test_chart_readout',selected)
        self.assertIn('tests.test_market_status',selected)
        self.assertNotIn('tests.test_ic_strategy',selected)
        self.assertNotIn('tests.test_etf_share_flows',selected)

    def test_shared_shell_requires_cross_page_checks(self):
        selected=q.select_tests(['dashboard-shell.css','tests/test_chart_readout.py'])
        self.assertIn('tests.test_ic_strategy',selected)
        self.assertIn('tests.test_market_cycle',selected)
        self.assertEqual(len(selected),len(set(selected)))

    def test_deploy_with_later_data_commit_counts_as_containing_release(self):
        self.assertTrue(q.descendant('wanted','wanted',lambda p:self.fail('no call')))
        self.assertTrue(q.descendant('wanted','later',lambda p:{'status':'ahead'}))
        self.assertFalse(q.descendant('wanted','older',lambda p:{'status':'behind'}))
        self.assertFalse(q.descendant('wanted','other',lambda p:{'status':'diverged'}))

if __name__=='__main__':unittest.main()
