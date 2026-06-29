import unittest

from scripts.ingest_wechat_url import extract_doc


class IngestWechatUrlTests(unittest.TestCase):
    def test_extract_doc_from_wechat_html(self):
        html = """
        <html>
        <head>
          <meta property="og:description" content="一篇测试摘要">
          <script>
            var msg_title = '测试文章';
            var nickname = '测试公众号';
            var biz = 'biz123';
          </script>
        </head>
        <body>
          <h1 id="activity-name">备用标题</h1>
          <a id="js_name">测试公众号</a>
          <em id="publish_time">2026-06-29 10:00</em>
          <div id="js_content"><p>正文第一段</p><p>正文第二段</p></div>
          <script>
            var appmsgid = '';
            window.cgiData = {biz: "biz123", mid: "456", idx: "1", sn: "abc"};
          </script>
        </body>
        </html>
        """

        doc = extract_doc("https://mp.weixin.qq.com/s/test", "https://mp.weixin.qq.com/s/test", html)

        self.assertEqual("测试文章", doc["title"])
        self.assertEqual("wechat:测试公众号", doc["source"])
        self.assertEqual("2026-06-29 10:00", doc["published_at"])
        self.assertEqual("正文第一段 正文第二段", doc["content"])
        self.assertEqual("manual_url", doc["ingest_mode"])
        self.assertIn("__biz=biz123", doc["url"])


if __name__ == "__main__":
    unittest.main()
