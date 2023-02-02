"""Proof Audit admin routes."""

from aiohttp import web
from aiohttp_apispec import docs, request_schema

from marshmallow import fields, Schema

from .manager import AuditProofManager


# TODO: Create method in AgentSchema to extract this raw schema instead of duplicating
class AuditProofRequestSchema(Schema):
    """AuditProof schema class."""

    presentation_request = fields.Dict(
        required=False,
        description="(Indy) presentation request (also known as proof request)",
    )
    presentation = fields.Dict(
        required=False, description="(Indy) presentation (also known as proof)"
    )


@docs(tags=["audit-proof"], summary="Verify a completed proof/presentation")
@request_schema(AuditProofRequestSchema())
async def audit_proof_verify(request: web.BaseRequest):
    """
    Request handler for verifying a completed proof/presentation.

    Args:
        request: aiohttp request object

    """
    context = request.app["request_context"]
    outbound_handler = request.app["outbound_message_router"]
    body = await request.json()

    presentation_request = body.get("presentation_request")
    presentation = body.get("presentation")

    audit_proof_manager = AuditProofManager(context)

    verified = await audit_proof_manager.verify_presentation(
        presentation_request, presentation
    )
    verified_response = {"verified": verified}

    return web.json_response(verified_response)


async def register(app: web.Application):
    """Register routes."""

    app.add_routes(
        [
            web.post(
                "/audit-proof/verify-presentation",
                audit_proof_verify,
            ),
        ]
    )


def post_process_routes(app: web.Application):
    """Amend swagger API."""

    # Add top-level tags description
    if "tags" not in app._state["swagger_dict"]:
        app._state["swagger_dict"]["tags"] = []
    app._state["swagger_dict"]["tags"].append(
        {
            "name": "audit-proof",
            "description": "Audit proof presentation",
        }
    )

