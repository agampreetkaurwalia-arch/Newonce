import unittest
from datetime import datetime, timedelta, timezone
from job_radar import Job, parse_date, normalize, classify, SOURCES, CUSTOM

from job_radar import NEEDS_ADAPTER, VERIFIED_BOARD_EVIDENCE

class RadarTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 9, 34, tzinfo=timezone.utc)
        self.start = self.now - timedelta(hours=24)

    def test_company_count(self):
        self.assertEqual(sum(map(len, SOURCES.values())) + len(CUSTOM) + len(NEEDS_ADAPTER), 53)

    def test_corrected_ats_mappings(self):
        self.assertEqual(SOURCES['greenhouse']['Automattic'], 'automatticcareers')
        for name, token in {'Buffer':'buffer', 'DuckDuckGo':'duck-duck-go',
                            'Zapier':'zapier', 'Kit':'kit', 'Contra':'contra',
                            'TestGorilla':'testgorilla', 'Constructor':'constructor',
                            'Chili Piper':'chilipiper', 'Toggl':'toggl'}.items():
            self.assertEqual(SOURCES['ashby'][name], token)
            self.assertIn(name, VERIFIED_BOARD_EVIDENCE)

    def test_company_names_unique(self):
        names=[name for boards in SOURCES.values() for name in boards]
        names += list(CUSTOM)+list(NEEDS_ADAPTER)
        self.assertEqual(len(names),len(set(names)))

    def test_millisecond_epoch(self):
        self.assertEqual(parse_date(1793698440000), self.now)

    def test_no_clock_timestamp_is_unverified(self):
        self.assertIsNone(parse_date('2026-11-03'))

    def test_lever_created_recent(self):
        j = normalize('lever','Buffer',{'id':'123','text':'Product Manager', 'categories':{'location':'Remote'},
                    'createdAt':int((self.now-timedelta(hours=2)).timestamp()*1000),
                    'hostedUrl':'https://jobs.lever.co/buffer/123'})
        self.assertEqual(classify(j,set(),True,self.start,self.now),'CONFIRMED_24H')

    def test_old_created_not_new(self):
        j = Job('A','lever','1','PM','Remote','https://example.com',
                created_at=(self.start-timedelta(seconds=1)).isoformat())
        self.assertIsNone(classify(j,set(),True,self.start,self.now))

    def test_greenhouse_update_not_creation(self):
        j = normalize('greenhouse','Alpaca',{'id':42,'title':'Designer',
                      'location':{'name':'Remote'},'absolute_url':'https://job-boards.greenhouse.io/alpaca/jobs/42',
                      'updated_at':self.now.isoformat()})
        self.assertEqual(classify(j,set(),True,self.start,self.now),'RECENT_VERIFY_FIRST_SEEN')
        self.assertIsNone(classify(j,set(),False,self.start,self.now))

    def test_ashby_unlisted(self):
        self.assertIsNone(normalize('ashby','Ghost',{'id':'x','title':'Designer',
                      'jobUrl':'https://jobs.ashbyhq.com/ghost/x','isListed':False}))

    def test_ashby_published(self):
        j = normalize('ashby','Ghost',{'id':'x','title':'Designer', 'location':'Remote',
                      'jobUrl':'https://jobs.ashbyhq.com/ghost/x', 'publishedAt':self.now.isoformat()})
        self.assertEqual(classify(j,set(),True,self.start,self.now),'RECENT_VERIFY_PUBLICATION')

    def test_existing_suppressed(self):
        j = Job('A','lever','1','PM','Remote','https://example.com', created_at=self.now.isoformat())
        self.assertIsNone(classify(j,{j.key},True,self.start,self.now))

if __name__ == '__main__':
    unittest.main()

class AdditionalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 9, 34, tzinfo=timezone.utc)
        self.start = self.now - timedelta(hours=24)

    def test_greenhouse_first_published_is_not_creation(self):
        j = normalize('greenhouse','Automattic',{'id':11,'title':'Product Manager',
           'location':{'name':'Remote'},'absolute_url':'https://job-boards.greenhouse.io/automatticcareers/jobs/11',
           'updated_at':self.now.isoformat(),'first_published':(self.now-timedelta(hours=2)).isoformat()})
        self.assertEqual(classify(j,set(),False,self.start,self.now),'RECENT_VERIFY_PUBLICATION')

    def test_greenhouse_old_publication_updated_recently_excluded(self):
        j = normalize('greenhouse','Automattic',{'id':12,'title':'Product Manager',
           'location':{'name':'Remote'},'absolute_url':'https://example.org/job/12',
           'updated_at':self.now.isoformat(),'first_published':(self.now-timedelta(days=10)).isoformat()})
        self.assertIsNone(classify(j,set(),True,self.start,self.now))

    def test_pinpoint_normalization(self):
        j = normalize('pinpoint', 'SafetyWing', {'id':'aa','title':'Product Manager',
            'location':{'name':'Global'},'workplace_type':'remote',
            'url':'https://safetywing.pinpointhq.com/en/postings/abc'})
        self.assertEqual(j.location,'Global (Remote)')
        self.assertEqual(j.key,'pinpoint:SafetyWing:aa')

    def test_publication_older_than_24h_not_first_seen(self):
        j = Job('A','ashby','1','Product Manager','Remote','https://example.org',
                published_at=(self.start-timedelta(minutes=1)).isoformat())
        self.assertIsNone(classify(j,set(),True,self.start,self.now))

    def test_just_outside_creation_window(self):
        j = Job('A','lever','1','Product Manager','Remote','https://example.org',
                created_at=(self.start-timedelta(microseconds=1)).isoformat())
        self.assertIsNone(classify(j,set(),True,self.start,self.now))

    def test_pinpoint_is_configured(self):
        self.assertEqual(SOURCES['pinpoint']['SafetyWing'], 'safetywing')
        self.assertEqual(SOURCES['greenhouse']['Sporty Group'], 'sportygroup')
        self.assertEqual(SOURCES['lever']['Kinsta'], 'kinsta')
        self.assertEqual(SOURCES['workable']['Modash'], 'modash')
