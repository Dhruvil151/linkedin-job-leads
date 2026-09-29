import sys
import unittest
from unittest.mock import Mock, patch

import main


class CollectionOnlyTests(unittest.TestCase):
    def test_collection_only_does_not_require_or_read_a_resume(self):
        scraper = Mock()
        scraper.scrape_posts.return_value = []
        with patch.object(sys, 'argv', ['main.py', '--skip-score']), \
                patch.object(main, '_setup_logging'), \
                patch.object(main, '_find_latest_resume') as find_resume, \
                patch.dict(sys.modules, {'scrape_posts': scraper}):
            main.main()
        scraper.scrape_posts.assert_called_once()
        find_resume.assert_not_called()
