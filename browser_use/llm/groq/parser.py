import json
import re
from typing import TypeVar

from groq import APIStatusError
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


def try_parse_groq_failed_generation(e: APIStatusError, output_format: type[T]) -> T:
	"""
	Known issue with Groq is that it sometimes fails to generate a valid JSON object,
	but the content is still in the response body. This function tries to extract the
	content and parse it as the output format.
	"""
	# Extract the response text from the exception
	response_text = e.response.text

	# Find the JSON part of the response
	# This regex looks for a JSON object that might be embedded in the text
	match = re.search(r'\{.*\}', response_text, re.DOTALL)
	if not match:
		raise ValueError('No JSON object found in the response text')

	json_text = match.group(0)

	# Clean up the JSON text (e.g., remove trailing commas)
	json_text = re.sub(r',\s*\}', '}', json_text)
	json_text = re.sub(r',\s*\]', ']', json_text)

	# Parse the JSON and validate with the Pydantic model
	parsed_json = json.loads(json_text)
	return output_format.model_validate(parsed_json)
