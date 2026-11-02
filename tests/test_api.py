"""
Unit tests for API endpoints and AWS Lambda handler input validation.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from aws.lambda_function import lambda_handler


class TestApiAndLambda(unittest.TestCase):
    def test_health_endpoint(self):
        event = {
            "httpMethod": "GET",
            "path": "/health",
        }
        res = lambda_handler(event, None)
        self.assertEqual(res["statusCode"], 200)
        body = json.loads(res["body"])
        self.assertEqual(body["status"], "HEALTHY")
        self.assertIn("model", body)

    def test_catalog_search(self):
        event = {
            "httpMethod": "GET",
            "path": "/books",
            "queryStringParameters": {
                "q": "B08510",
                "limit": "5",
            },
        }
        res = lambda_handler(event, None)
        self.assertEqual(res["statusCode"], 200)
        body = json.loads(res["body"])
        self.assertGreaterEqual(body["returned_count"], 1)
        self.assertEqual(body["books"][0]["book_id"], "B08510")

    def test_predict_post_valid_payload(self):
        event = {
            "httpMethod": "POST",
            "path": "/predict",
            "body": json.dumps({
                "book_id": "B08510",
                "available_copies": 1,
                "days_to_first_exam": 7,
                "exam_type": "CAT2",
            }),
        }
        res = lambda_handler(event, None)
        self.assertEqual(res["statusCode"], 200)
        body = json.loads(res["body"])
        self.assertEqual(body["book_id"], "B08510")
        self.assertIn("stockout_assessment", body)
        self.assertIn("ml_prediction", body)

    def test_predict_missing_book_id_bad_request(self):
        event = {
            "httpMethod": "POST",
            "path": "/predict",
            "body": json.dumps({
                "available_copies": 2,
            }),
        }
        res = lambda_handler(event, None)
        self.assertEqual(res["statusCode"], 400)
        body = json.loads(res["body"])
        self.assertIn("error", body)

    def test_cors_preflight(self):
        event = {
            "httpMethod": "OPTIONS",
            "path": "/predict",
        }
        res = lambda_handler(event, None)
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(res["headers"]["Access-Control-Allow-Origin"], "*")


if __name__ == "__main__":
    unittest.main()
