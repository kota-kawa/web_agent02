import pytest
from unittest.mock import MagicMock
from flask_app.webarena.evaluation import WebArenaEvaluator

class TestWebArenaEvaluator:
    @pytest.fixture
    def mock_controller(self):
        controller = MagicMock()
        return controller

    def test_evaluate_string_match_success(self, mock_controller):
        evaluator = WebArenaEvaluator(mock_controller)
        task = {
            "eval": {
                "eval_types": ["string_match"],
                "reference_answers": {
                    "exact_match": "Order #123"
                }
            }
        }
        history = MagicMock()
        final_result = "Order #123"

        result = evaluator.evaluate(task, history, final_result)
        assert "Success" in result
        assert "Exact match found" in result

    def test_evaluate_string_match_fail(self, mock_controller):
        evaluator = WebArenaEvaluator(mock_controller)
        task = {
            "eval": {
                "eval_types": ["string_match"],
                "reference_answers": {
                    "exact_match": "Order #123"
                }
            }
        }
        history = MagicMock()
        final_result = "Order #999"

        result = evaluator.evaluate(task, history, final_result)
        assert "Failure" in result
        assert "Expected exact match" in result

    def test_evaluate_url_match_success(self, mock_controller):
        mock_controller.evaluate_in_browser.return_value = "http://shopping/account"
        evaluator = WebArenaEvaluator(mock_controller)
        task = {
            "eval": {
                "eval_types": ["url_match"],
                "reference_url": "shopping/account"
            }
        }
        history = MagicMock()

        result = evaluator.evaluate(task, history, "")
        assert "Success" in result
        assert "Current URL matches reference" in result

    def test_evaluate_program_html_success(self, mock_controller):
        # Mock JS execution result
        mock_controller.evaluate_in_browser.return_value = "$500.00"

        evaluator = WebArenaEvaluator(mock_controller)
        task = {
            "eval": {
                "eval_types": ["program_html"],
                "program_html": [
                    {
                        "locator": "document.querySelector('.price').innerText",
                        "required_contents": {
                            "exact_match": "$500.00"
                        }
                    }
                ]
            }
        }
        history = MagicMock()

        result = evaluator.evaluate(task, history, "")

        # Verify correct JS call
        call_args = mock_controller.evaluate_in_browser.call_args[0][0]
        assert "document.querySelector('.price').innerText" in call_args

        assert "Success" in result
        assert "Exact match for locator" in result

    def test_evaluate_program_html_must_include(self, mock_controller):
        mock_controller.evaluate_in_browser.return_value = "The price is $500.00 today"

        evaluator = WebArenaEvaluator(mock_controller)
        task = {
            "eval": {
                "eval_types": ["program_html"],
                "program_html": [
                    {
                        "locator": "document.body.innerText",
                        "required_contents": {
                            "must_include": ["$500.00"]
                        }
                    }
                ]
            }
        }
        history = MagicMock()

        result = evaluator.evaluate(task, history, "")
        assert "Success" in result
        assert "Required content found" in result

    def test_evaluate_program_html_fail(self, mock_controller):
        mock_controller.evaluate_in_browser.return_value = "Out of Stock"

        evaluator = WebArenaEvaluator(mock_controller)
        task = {
            "eval": {
                "eval_types": ["program_html"],
                "program_html": [
                    {
                        "locator": "document.body.innerText",
                        "required_contents": {
                            "exact_match": "In Stock"
                        }
                    }
                ]
            }
        }
        history = MagicMock()

        result = evaluator.evaluate(task, history, "")
        assert "Failure" in result
        assert "Expected 'In Stock', got 'Out of Stock'" in result
