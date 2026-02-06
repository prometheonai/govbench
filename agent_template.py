import logging
import json
from typing import Any, List
from enum import Enum

from shared_volume.functions.retry_decorator import retry_on_exception
from shared_volume.agents.utils.utils import call_llm, trim_response_keep_delimiters, remove_think_tags
from shared_volume.agents.constants import ERROR_JSON_FORMAT, ERROR_NO_RESPONSE
from shared_volume.agents.config import MAX_AGENT_RETRIES

class AgentOutput(str, Enum):
    TEXT = "text"
    BOOLEAN = "boolean"
    PERCENTAGE = "percentage"
    JSON = "json"

async def agent_template(system_prompt: str, user_prompt: str, output_type: AgentOutput, log: logging.Logger = None):
    try:
        if not system_prompt or not user_prompt:
            raise ValueError("System prompt and user prompt are required")
        if not output_type or not isinstance(output_type, AgentOutput):
            raise ValueError("Output type is required")

        system_prompt = append_format_to_prompt(system_prompt, output_type)
        return await execute_agent(system_prompt, user_prompt, output_type, log)

    except ValueError as e:
        if log:
            log.error(
                f"Error executing agent: {e}. "
                f"Output type: {output_type}, "
                f"System prompt length: {len(system_prompt)}, "
                f"User prompt: {user_prompt}"
            )
        if output_type == AgentOutput.TEXT:
            return ""
        if output_type == AgentOutput.BOOLEAN:
            return False
        if output_type == AgentOutput.JSON:
            return []
        if output_type == AgentOutput.PERCENTAGE:
            return 0
       

@retry_on_exception(max_retries=MAX_AGENT_RETRIES, delay=1, backoff=1.2)
async def execute_agent(system_prompt: str, user_prompt: str, output_type: AgentOutput, log: logging.Logger = None):

    response = await call_llm(system_prompt, user_prompt)
    if not response:
        raise ValueError(ERROR_NO_RESPONSE)

    response = remove_think_tags(response)

    if output_type == AgentOutput.TEXT:
        return validate_text_output(response)

    if output_type == AgentOutput.BOOLEAN:
        return validate_boolean_output(response)

    if output_type == AgentOutput.PERCENTAGE:
        return validate_percentage_output(response)

    if output_type == AgentOutput.JSON:
        return validate_json_output(response, log)

def validate_text_output(response: str) -> str:
    return response.strip()

def validate_boolean_output(response: str) -> bool:
    response = response.lower().strip()
    if "yes" in response:
        return True
    if "no" in response:
        return False
    raise ValueError(f"Invalid boolean response from LLM: {response}")

def validate_percentage_output(response: str) -> int:
    percentage = int(response.strip().replace("%", ""))
    if percentage < 0 or percentage > 100:
        raise ValueError(f"Invalid percentage response from LLM: {response}")
    return percentage

def validate_json_output(response: str, log: logging.Logger = None) -> List:
    json_output = trim_response_keep_delimiters(response, "[", "]")
    if not json_output:
        json_output = trim_response_keep_delimiters(response, "{", "}")
    if not json_output:
        raise ValueError(ERROR_JSON_FORMAT)

    try:
        parsed = json.loads(json_output)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]
        else:
            return [parsed]
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to decode JSON: {e}") from e

PERCENTAGE_FORMAT = """OUTPUT FORMAT:\nYour entire response must be *only* the number (e.g., "85"). Do not add any preamble, explanation, or the "%" symbol."""
BOOLEAN_FORMAT = """OUTPUT FORMAT:\nYour response must be *only* "yes" or "no". Do not add any preamble, explanation, or additional text."""
TEXT_FORMAT = """OUTPUT FORMAT:\nInmediately respond with the answer. Do not add any preamble, explanation, or additional text."""
JSON_FORMAT = """OUTPUT FORMAT:\nYour response must be valid JSON. Do not add any preamble, explanation, or additional text."""

def append_format_to_prompt(prompt: str, output_type: AgentOutput) -> str:
    if output_type == AgentOutput.PERCENTAGE:
        return f"{prompt}\n\n{PERCENTAGE_FORMAT}"
    if output_type == AgentOutput.BOOLEAN:
        return f"{prompt}\n\n{BOOLEAN_FORMAT}"
    if output_type == AgentOutput.TEXT:
        return f"{prompt}\n\n{TEXT_FORMAT}"
    if output_type == AgentOutput.JSON:
        return f"{prompt}\n\n{JSON_FORMAT}"
