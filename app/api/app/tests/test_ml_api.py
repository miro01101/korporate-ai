from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app

RUN_ID = UUID('2425d5eb-371f-48d1-9d60-a65bcf614d74')
FEATURE_ID = UUID('8d09cb22-5f1c-4312-9134-fdb9410498ab')
REC_ID = UUID('11111111-1111-5111-8111-111111111111')


def forecast_item():
    return {
        'model_run_id': str(RUN_ID), 'product_id':'P1', 'forecast_month':'2026-01-01',
        'horizon':1, 'forecast_p10':1.0, 'forecast_p50':2.0, 'forecast_p90':3.0,
        'selected_model':'naive', 'is_cold_start':False, 'backtest_wape':0.1,
        'backtest_bias':0.0, 'confidence_score':0.5, 'created_at':'2026-01-01T00:00:00+00:00'
    }


def risk_item():
    return {
        'model_run_id':str(RUN_ID),'product_id':'P1','as_of_date':'2025-12-01',
        'stock_available':1,'incoming_quantity':0,'expected_lead_time_demand':2.0,
        'safety_stock':1.0,'reorder_point':3.0,'stockout_probability_30d':0.5,
        'stockout_probability_60d':0.6,'stockout_probability_90d':0.7,
        'overstock_probability_90d':0.0,'recommended_order_quantity':2,
        'recommended_order_date':'2025-12-01','created_at':'2026-01-01T00:00:00+00:00'
    }


def rec_item():
    return {
        'id':str(REC_ID),'model_run_id':str(RUN_ID),'product_id':'P1',
        'recommendation_type':'PURCHASE','priority':80,'recommended_action':'Objednat',
        'recommended_quantity':2,'recommended_date':'2025-12-01','expected_value_eur':None,
        'risk_if_ignored_eur':None,'confidence':0.5,'reason_codes':['REORDER_REQUIRED'],
        'explanation':'Test','status':'pending','created_at':'2026-01-01T00:00:00+00:00',
        'updated_at':'2026-01-01T00:00:00+00:00'
    }


class MLApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_openapi_contains_six_ml_get_routes(self):
        doc=self.client.get('/openapi.json').json()
        paths=doc['paths']
        expected={'/api/v1/ml/status','/api/v1/ml/model-runs','/api/v1/ml/forecast',
                  '/api/v1/ml/inventory-risk','/api/v1/ml/recommendations',
                  '/api/v1/ml/products/{product_id}'}
        self.assertTrue(expected.issubset(paths))
        for path in expected:
            self.assertEqual(set(paths[path]), {'get'})

    @patch('app.ml_api.fetch_one_readonly')
    def test_status(self, fetch_one):
        fetch_one.return_value={
            'platform_version':'0.5.0','latest_model_run_id':str(RUN_ID),
            'model_family':'hybrid_calibrated','model_version':'v1',
            'training_cutoff':'2025-12-01','forecast_horizon_months':3,
            'forecast_rows':240,'inventory_risk_rows':80,'recommendation_rows':80,
            'pending_recommendations':80,'transaction_read_only':True,
        }
        response=self.client.get('/api/v1/ml/status')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['api_version'],'0.5.0')
        self.assertTrue(response.json()['transaction_read_only'])

    @patch('app.ml_api.fetch_all_readonly')
    def test_model_runs(self, fetch_all):
        fetch_all.return_value=[{
            'id':str(RUN_ID),'feature_run_id':str(FEATURE_ID),'status':'completed',
            'model_family':'hybrid_calibrated','model_version':'v1',
            'training_cutoff':'2025-12-01','forecast_horizon_months':3,
            'feature_version':'f1','code_commit':None,'dataset_fingerprint':'x',
            'parameters':{},'artifact_path':None,'artifact_sha256':None,
            'started_at':'2026-01-01T00:00:00+00:00','finished_at':'2026-01-01T00:00:01+00:00',
            'error_message':None,
        }]
        response=self.client.get('/api/v1/ml/model-runs')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['count'],1)

    @patch('app.ml_api.resolve_model_run_id', return_value=RUN_ID)
    @patch('app.ml_api.fetch_all_readonly', return_value=[forecast_item()])
    def test_forecast(self, *_):
        response=self.client.get('/api/v1/ml/forecast')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['items'][0]['horizon'],1)

    @patch('app.ml_api.resolve_model_run_id', return_value=RUN_ID)
    @patch('app.ml_api.fetch_all_readonly', return_value=[risk_item()])
    def test_inventory_risk(self, *_):
        response=self.client.get('/api/v1/ml/inventory-risk')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['items'][0]['recommended_order_quantity'],2)

    @patch('app.ml_api.resolve_model_run_id', return_value=RUN_ID)
    @patch('app.ml_api.fetch_all_readonly', return_value=[rec_item()])
    def test_recommendations(self, *_):
        response=self.client.get('/api/v1/ml/recommendations')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['items'][0]['status'],'pending')

    @patch('app.ml_api.resolve_model_run_id', return_value=RUN_ID)
    @patch('app.ml_api.fetch_one_readonly', return_value=None)
    def test_product_not_found(self, *_):
        response=self.client.get('/api/v1/ml/products/UNKNOWN')
        self.assertEqual(response.status_code,404)

    @patch('app.ml_api.resolve_model_run_id', return_value=RUN_ID)
    @patch('app.ml_api.fetch_all_readonly', return_value=[forecast_item()])
    @patch('app.ml_api.fetch_one_readonly')
    def test_product_detail(self, fetch_one, *_):
        fetch_one.side_effect=[
            {'product_id':'P1','product_name':'P','category':'C','unit':'ks',
             'purchase_price':1.0,'sales_price':2.0,'supplier_id':1,
             'minimum_order_quantity':1,'lead_time_days':7,'weight_kg':1.0,'volume_m3':0.1},
            risk_item(), rec_item(),
        ]
        response=self.client.get('/api/v1/ml/products/P1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()['forecasts']),1)

    def test_ml_routes_reject_post(self):
        response=self.client.post('/api/v1/ml/recommendations', json={})
        self.assertEqual(response.status_code,405)

    def test_readonly_execution_option_is_present(self):
        import inspect
        from app import ml_api
        source=inspect.getsource(ml_api.fetch_all_readonly)
        self.assertIn('postgresql_readonly=True', source)

if __name__=='__main__': unittest.main()
