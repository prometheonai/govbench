import logging
import time
import asyncio
from typing import Dict, Any
from backend.db.chats.crud import Chats
from shared_volume.send_alerts import send_alert
import json

from fastapi import HTTPException
from starlette.responses import StreamingResponse
from opentelemetry import trace

from backend.db.users.models import UserModel

from backend.services.search.search_only_sources import retrieve_sources
from backend.services.search.utils.trim_for_context_size import count_tokens, count_payload_tokens
from backend.services.agent_router.main import orchestrator_agent
from shared_volume.agents.eval_verification_agent import eval_verification_agent, eval_verification_score_agent, eval_neutrality_agent, eval_neutrality_score_agent, eval_security_agent, eval_security_score_agent, eval_usability_agent, eval_usability_score_agent, eval_relevance_agent, eval_relevance_score_agent
from shared_volume.agents.utils.utils import remove_think_tags
from shared_volume.agents.utils.utils import call_llm
from backend.apps.generation.utils import (
    select_system_prompt,
    validate_payload,
    validate_user_request_limits,
    manage_title_length,
)
from backend.services.tracking_tasks import run_task_and_track,empty_stream
from backend.apps.generation.schemas import GenerateChatCompletionForm, GetSourcesForm, GenerateTitleForm, GenerateEvaluationForm
from backend.services.QueryQueue import query_queue
from backend.utils.MetricManager import metric_manager
from backend.config import SERVICE_NAME, LLM_CLIENT, LLM_NAME
from backend.apps.generation.prompts import TITLE_GENERATION_SYSTEM_PROMPT, TITLE_GENERATION_USER_PROMPT
from shared_volume.error_mapper import map_llm_error


log = logging.getLogger(SERVICE_NAME)
tracer = trace.get_tracer(SERVICE_NAME)

# THEON

async def generate_evaluation(form_data: GenerateEvaluationForm) -> Dict[str, Any]:
    res = {
        "relevance": "No relevance evaluation generated",
        "relevance_score": 0,
        "usability": "No usability evaluation generated",
        "usability_score": 0,
        "neutrality": "No neutrality evaluation generated",
        "neutrality_score": 0,
        "security": "No security evaluation generated",
        "security_score": 0,
        "verification": "No verification evaluation generated",
        "verification_score": 0,
        "total_score": 0,
        "lowest_score": 0
    }
    
    try:
        q = form_data.question or ""
        a = form_data.answer or ""
        
        if not q.strip() or not a.strip():
            return res

        relevance = await eval_relevance_agent(a, q)
        r_score = await eval_relevance_score_agent(relevance) or 0
        log.info(f"Relevance: {r_score}")

        neutrality = await eval_neutrality_agent(a, q)
        n_score = await eval_neutrality_score_agent(neutrality) or 0
        log.info(f"Neutrality: {n_score}")

        security = await eval_security_agent(a, q)
        s_score = await eval_security_score_agent(security) or 0
        log.info(f"Security: {s_score}")

        usability = await eval_usability_agent(a, q)
        u_score = await eval_usability_score_agent(usability) or 0
        log.info(f"Usability: {u_score}")

        sources = {"sources_db": form_data.sources_db, "sources_web": form_data.sources_web, "sources_verdic": form_data.sources_verdic}

        verification = await eval_verification_agent(a, q, sources, log)
        v_score = await eval_verification_score_agent(verification) or 0
        log.info(f"Verification: {v_score}")

        total_score = round((n_score + s_score + u_score + r_score + v_score) / 5)
        log.info(f"Total score: {total_score}")
        lowest_score = min(n_score, s_score, u_score, r_score, v_score)
        log.info(f"Lowest score: {lowest_score}")

        res.update({
            "relevance": relevance, "neutrality": neutrality, "security": security, "usability": usability, "verification": verification,
            "neutrality_score": n_score, "security_score": s_score, "usability_score": u_score, "relevance_score": r_score, "verification_score": v_score,
            "total_score": total_score, "lowest_score": lowest_score
        })

        chat_model = Chats.get_chat_by_id(form_data.chat_id)
        if chat_model:
            full_chat_data = json.loads(chat_model.chat)
            if "history" in full_chat_data and "messages" in full_chat_data["history"]:
                msg_dict = full_chat_data["history"]["messages"]
                if form_data.message_id in msg_dict:
                    msg_dict[form_data.message_id]["evaluation"] = res
                    Chats.update_chat_by_id(form_data.chat_id, full_chat_data)

    except Exception as e:
        log.error(f"Error in generate_evaluation: {str(e)}")
    
    return res