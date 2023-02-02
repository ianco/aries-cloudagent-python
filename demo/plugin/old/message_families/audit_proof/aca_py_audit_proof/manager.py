"""Classes to support proof audit."""

import logging

from aries_cloudagent.config.injection_context import InjectionContext
from aries_cloudagent.core.error import BaseError
from aries_cloudagent.revocation.models.revocation_registry import RevocationRegistry
from aries_cloudagent.config.injection_context import InjectionContext
from aries_cloudagent.core.error import BaseError
from aries_cloudagent.ledger.base import BaseLedger
from aries_cloudagent.indy.verifier import IndyVerifier


class AuditProofManagerError(BaseError):
    """Audit Proof error."""


class AuditProofManager:
    """Class for providing proof audits."""

    def __init__(self, context: InjectionContext):
        """
        Initialize an AuditProofManager.

        Args:
            context: The context for this proof audit
        """
        self._context = context
        self._logger = logging.getLogger(__name__)

    @property
    def context(self) -> InjectionContext:
        """
        Accessor for the current injection context.

        Returns:
            The injection context for this connection

        """
        return self._context

    async def verify_presentation(
        self, presentation_request: dict, presentation: dict
    ):
        """
        Verify a presentation.

        Args:
            presentation_request: indy presentation request
            presentation: indy presentation to verify

        Returns:
            verification status

        """
        indy_proof_request = presentation_request
        indy_proof = presentation

        schema_ids = []
        credential_definition_ids = []

        schemas = {}
        credential_definitions = {}
        rev_reg_defs = {}
        rev_reg_entries = {}

        identifiers = indy_proof["identifiers"]
        ledger: BaseLedger = await self.context.inject(BaseLedger)
        async with ledger:
            for identifier in identifiers:
                schema_ids.append(identifier["schema_id"])
                credential_definition_ids.append(identifier["cred_def_id"])

                # Build schemas for anoncreds
                if identifier["schema_id"] not in schemas:
                    schemas[identifier["schema_id"]] = await ledger.get_schema(
                        identifier["schema_id"]
                    )

                if identifier["cred_def_id"] not in credential_definitions:
                    credential_definitions[
                        identifier["cred_def_id"]
                    ] = await ledger.get_credential_definition(
                        identifier["cred_def_id"]
                    )

                if identifier.get("rev_reg_id"):
                    if identifier["rev_reg_id"] not in rev_reg_defs:
                        rev_reg_defs[
                            identifier["rev_reg_id"]
                        ] = await ledger.get_revoc_reg_def(identifier["rev_reg_id"])

                    if identifier.get("timestamp"):
                        rev_reg_entries.setdefault(identifier["rev_reg_id"], {})

                        if (
                            identifier["timestamp"]
                            not in rev_reg_entries[identifier["rev_reg_id"]]
                        ):
                            (
                                found_rev_reg_entry,
                                _found_timestamp,
                            ) = await ledger.get_revoc_reg_entry(
                                identifier["rev_reg_id"], identifier["timestamp"]
                            )
                            rev_reg_entries[identifier["rev_reg_id"]][
                                identifier["timestamp"]
                            ] = found_rev_reg_entry

        verifier: BaseVerifier = await self.context.inject(BaseVerifier)
        verified = await verifier.verify_presentation(
            indy_proof_request,
            indy_proof,
            schemas,
            credential_definitions,
            rev_reg_defs,
            rev_reg_entries,
        )

        return verified
