import unittest
from datetime import datetime, timedelta, timezone
from job_radar import Job, parse_date, normalize, classify, SOURCES, CUSTOM

class RadarTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 9, 34, tzinfo=timezone.utc)
        self.start = self.now - timedelta(hours=24)

    def test_company_count(self):
        self.assertEqual(sum(map(len, SOURCES.values())) + len(CUSTOM), 53)

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
