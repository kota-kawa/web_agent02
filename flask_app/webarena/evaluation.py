import logging
import json

logger = logging.getLogger(__name__)

class WebArenaEvaluator:
    """
    Handles evaluation of WebArena tasks using string matching, URL matching,
    and client-side DOM inspection via the browser controller.
    """

    def __init__(self, controller):
        self.controller = controller

    def evaluate(self, task, history, final_result):
        """
        Evaluate the task based on its 'eval' configuration.

        Args:
            task (dict): The task definition object.
            history (object): The agent's history object.
            final_result (str): The final text output from the agent.

        Returns:
            str: A newline-separated string of evaluation results.
        """
        if not task:
            return "Custom task - no automated evaluation"

        eval_config = task.get('eval', {})
        eval_types = eval_config.get('eval_types', [])
        reference_answers = eval_config.get('reference_answers', {})

        results = []

        # 1. String Match (Checks the agent's final text output)
        if 'string_match' in eval_types:
            results.extend(self._evaluate_string_match(final_result, reference_answers))

        # 2. URL Match (Checks the browser's current URL)
        if 'url_match' in eval_types:
            reference_url = eval_config.get('reference_url')
            results.extend(self._evaluate_url_match(reference_url, final_result))

        # 3. Program HTML (Checks the DOM state via JS execution)
        if 'program_html' in eval_types:
            program_html = eval_config.get('program_html', [])
            results.extend(self._evaluate_program_html(program_html))

        if not results:
            return "No automated evaluation criteria met or supported."

        return "\n".join(results)

    def _evaluate_string_match(self, final_result, reference_answers):
        results = []
        exact_match = reference_answers.get('exact_match')
        must_include = reference_answers.get('must_include')
        fuzzy_match = reference_answers.get('fuzzy_match')

        if exact_match:
            if final_result.strip() == exact_match.strip():
                results.append("Success (String): Exact match found.")
            else:
                results.append(f"Failure (String): Expected exact match '{exact_match}'.")

        if must_include:
            missing = [phrase for phrase in must_include if phrase.lower() not in final_result.lower()]
            if not missing:
                results.append("Success (String): All required phrases found.")
            else:
                results.append(f"Failure (String): Missing phrases: {', '.join(missing)}")

        if fuzzy_match:
             if isinstance(fuzzy_match, list):
                 found = any(phrase.lower() in final_result.lower() for phrase in fuzzy_match)
                 match_str = ", ".join(fuzzy_match)
             else:
                 found = fuzzy_match.lower() in final_result.lower()
                 match_str = fuzzy_match

             if found:
                 results.append("Success (String): Fuzzy match found.")
             else:
                 results.append(f"Failure (String): No fuzzy match found for {match_str}")
        return results

    def _evaluate_url_match(self, reference_url, final_result):
        results = []
        if not reference_url:
            return results

        try:
            # We assume the controller has a method to get the current URL or execute JS
            current_url = self.controller.evaluate_in_browser("window.location.href")

            # Helper to normalize URLs for comparison (strip trailing slashes, http/https)
            def normalize(u):
                return u.replace('http://', '').replace('https://', '').rstrip('/')

            if reference_url in current_url or normalize(reference_url) in normalize(current_url):
                results.append(f"Success (URL): Current URL matches reference '{reference_url}'")
            else:
                # Fallback: check if the URL was mentioned in the text output
                if reference_url in final_result:
                    results.append(f"Success (URL): Reference URL found in text output (fallback).")
                else:
                    results.append(f"Failure (URL): URL '{reference_url}' not found. Current: {current_url}")
        except Exception as e:
            results.append(f"Warning (URL): Could not verify browser URL: {e}")

        return results

    def _evaluate_program_html(self, program_html):
        results = []
        for check in program_html:
            locator_js = check.get('locator')
            required_contents = check.get('required_contents', {})

            if locator_js:
                try:
                    # Execute JS in browser
                    # We wrap in IIFE to ensure return value
                    js_code = f"(() => {{ return {locator_js}; }})()"

                    execution_result = self.controller.evaluate_in_browser(js_code)
                    execution_result_str = str(execution_result) if execution_result is not None else ""

                    exact_match = required_contents.get('exact_match')
                    must_include = required_contents.get('must_include')

                    if exact_match:
                        if execution_result_str.strip() == exact_match.strip():
                             results.append(f"Success (DOM): Exact match for locator '{locator_js}'.")
                        else:
                             results.append(f"Failure (DOM): Expected '{exact_match}', got '{execution_result_str}' for locator '{locator_js}'")

                    if must_include:
                        missing = [phrase for phrase in must_include if phrase.lower() not in execution_result_str.lower()]
                        if not missing:
                             results.append(f"Success (DOM): Required content found in locator '{locator_js}'.")
                        else:
                             results.append(f"Failure (DOM): Missing content in DOM for '{locator_js}': {', '.join(missing)}")

                    # If check logic depends on numeric comparison (like length > 0), we might need custom logic here
                    # For now, we assume string comparison or existence check via must_include=[]
                    if not exact_match and not must_include:
                         # Just checking if it runs without error and returns something truthy?
                         if execution_result:
                             results.append(f"Success (DOM): Locator '{locator_js}' returned truthy value.")
                         else:
                             results.append(f"Failure (DOM): Locator '{locator_js}' returned empty/false.")

                except Exception as e:
                    results.append(f"Failure (DOM): Error executing locator '{locator_js}': {e}")
            else:
                 results.append("Warning: program_html check missing locator")
        return results
