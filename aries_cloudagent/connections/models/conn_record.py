"""Handle connection information interface with non-secrets storage."""

import json

from enum import Enum
from typing import Any, Union

from marshmallow import fields, validate

from ...config.injection_context import InjectionContext
from ...messaging.models.base_record import BaseRecord, BaseRecordSchema
from ...messaging.valid import INDY_DID, INDY_RAW_PUBLIC_KEY, UUIDFour

from ...protocols.connections.v1_0.message_types import CONNECTION_INVITATION
from ...protocols.connections.v1_0.messages.connection_invitation import (
    ConnectionInvitation,
)
from ...protocols.connections.v1_0.messages.connection_request import ConnectionRequest
from ...protocols.didcomm_prefix import DIDCommPrefix
from ...protocols.out_of_band.v1_0.messages.invitation import (
    Invitation as OOBInvitation,
)
from ...storage.base import BaseStorage
from ...storage.record import StorageRecord


class ConnRecord(BaseRecord):
    """Represents a single pairwise connection."""

    class Meta:
        """ConnRecord metadata."""

        schema_class = "ConnRecordSchema"

    class Role(Enum):
        """RFC 160 (inviter, invitee) = RFC 23 (responder, requester)."""

        REQUESTER = ("invitee", "requester")  # == RFC 23 initiator, RFC 434 receiver
        RESPONDER = ("inviter", "responder")  # == RFC 160 initiator(!), RFC 434 sender

        @property
        def rfc160(self):
            """Return RFC 160 (connection protocol) nomenclature."""
            return self.value[0]

        @property
        def rfc23(self):
            """Return RFC 23 (DID exchange protocol) nomenclature."""
            return self.value[1]

        @classmethod
        def get(cls, label: Union[str, "ConnRecord.Role"]):
            """Get role enum for label."""
            if isinstance(label, str):
                for role in ConnRecord.Role:
                    if label in role.value:
                        return role
            elif isinstance(label, ConnRecord.Role):
                return label
            return None

        def flip(self):
            """Return interlocutor role."""
            return (
                ConnRecord.Role.REQUESTER
                if self is ConnRecord.Role.RESPONDER
                else ConnRecord.Role.RESPONDER
            )

        def __eq__(self, other: Union[str, "ConnRecord.Role"]) -> bool:
            """Comparison between roles."""
            return self is ConnRecord.Role.get(other)

    class State(Enum):
        """Collator for equivalent states between RFC 160 and RFC 23."""

        INIT = ("init", "start")
        INVITATION = ("invitation", "invitation")
        REQUEST = ("request", "request")
        RESPONSE = ("response", "response")
        COMPLETED = ("active", "completed")
        ABANDONED = ("error", "abandoned")

        @property
        def rfc160(self):
            """Return RFC 160 (connection protocol) nomenclature."""
            return self.value[0]

        @property
        def rfc23(self):
            """Return RFC 23 (DID exchange protocol) nomenclature."""
            return self.value[1]

        @classmethod
        def get(cls, label: Union[str, "ConnRecord.State"]):
            """Get state enum for label."""
            if isinstance(label, str):
                for state in ConnRecord.State:
                    if label in state.value:
                        return state
            elif isinstance(label, ConnRecord.State):
                return label
            return None

        def __eq__(self, other: Union[str, "ConnRecord.State"]) -> bool:
            """Comparison between states."""
            return self is ConnRecord.State.get(other)

    RECORD_ID_NAME = "connection_id"
    WEBHOOK_TOPIC = "connections"
    LOG_STATE_FLAG = "debug.connections"
    CACHE_ENABLED = True
    TAG_NAMES = {"my_did", "their_did", "request_id", "invitation_key"}

    RECORD_TYPE = "connection"
    RECORD_TYPE_INVITATION = "connection_invitation"
    RECORD_TYPE_REQUEST = "connection_request"

    INVITATION_MODE_ONCE = "once"
    INVITATION_MODE_MULTI = "multi"
    INVITATION_MODE_STATIC = "static"

    ROUTING_STATE_NONE = "none"
    ROUTING_STATE_REQUEST = "request"
    ROUTING_STATE_ACTIVE = "active"
    ROUTING_STATE_ERROR = "error"

    ACCEPT_MANUAL = "manual"
    ACCEPT_AUTO = "auto"

    def __init__(
        self,
        *,
        connection_id: str = None,
        my_did: str = None,
        their_did: str = None,
        their_label: str = None,
        tx_my_role:list = [],
        tx_their_role:list = [],
        initiator: str = None,
        their_role: Union[str, "ConnRecord.Role"] = None,
        invitation_key: str = None,
        request_id: str = None,
        state: Union[str, "ConnRecord.State"] = None,
        inbound_connection_id: str = None,
        error_msg: str = None,
        routing_state: str = None,
        accept: str = None,
        invitation_mode: str = None,
        alias: str = None,
        **kwargs,
    ):
    
        """Initialize a new ConnRecord."""
        super().__init__(
            connection_id,
            state=(ConnRecord.State.get(state) or ConnRecord.State.INIT).rfc160,
            **kwargs,
        )
        self.my_did = my_did
        self.their_did = their_did
        self.their_label = their_label
        self.their_role = (
            ConnRecord.Role.get(their_role).rfc160
            if isinstance(their_role, str)
            else None
            if their_role is None
            else their_role.rfc160
        )
        self.invitation_key = invitation_key
        self.tx_my_role = tx_my_role
        self.tx_their_role = tx_their_role
        self.request_id = request_id
        self.error_msg = error_msg
        self.inbound_connection_id = inbound_connection_id
        self.routing_state = routing_state or self.ROUTING_STATE_NONE
        self.accept = accept or self.ACCEPT_MANUAL
        self.invitation_mode = invitation_mode or self.INVITATION_MODE_ONCE
        self.alias = alias


    @property
    def connection_id(self) -> str:
        """Accessor for the ID associated with this connection."""
        return self._id

    @property
    def record_value(self) -> dict:
        """Accessor to for the JSON record value properties for this connection."""
        return {
            prop: getattr(self, prop)
            for prop in (
                "tx_my_role",
                "tx_their_role",
                "their_role",
                "inbound_connection_id",
                "routing_state",
                "accept",
                "invitation_mode",
                "alias",
                "error_msg",
                "their_label",
                "state",
            )
        }

    @classmethod
    async def retrieve_by_did(
        cls,
        context: InjectionContext,
        their_did: str = None,
        my_did: str = None,
        their_role: str = None,
    ) -> "ConnRecord":
        """Retrieve a connection record by target DID.

        Args:
            context: The injection context to use
            their_did: The target DID to filter by
            my_did: One of our DIDs to filter by
            my_role: Filter connections by their role
        """
        tag_filter = {}
        if their_did:
            tag_filter["their_did"] = their_did
        if my_did:
            tag_filter["my_did"] = my_did

        post_filter = {}
        if their_role:
            post_filter["their_role"] = cls.Role.get(their_role).rfc160

        return await cls.retrieve_by_tag_filter(context, tag_filter, post_filter)

    @classmethod
    async def retrieve_by_invitation_key(
        cls, context: InjectionContext, invitation_key: str, their_role: str = None
    ) -> "ConnRecord":
        """Retrieve a connection record by invitation key.

        Args:
            context: The injection context to use
            invitation_key: The key on the originating invitation
            initiator: Filter by the initiator value
        """
        tag_filter = {"invitation_key": invitation_key}
        post_filter = {"state": cls.State.INVITATION.rfc160}

        if their_role:
            post_filter["their_role"] = cls.Role.get(their_role).rfc160

        return await cls.retrieve_by_tag_filter(context, tag_filter, post_filter)

    @classmethod
    async def retrieve_by_request_id(
        cls, context: InjectionContext, request_id: str
    ) -> "ConnRecord":
        """Retrieve a connection record from our previous request ID.

        Args:
            context: The injection context to use
            request_id: The ID of the originating connection request
        """
        tag_filter = {"request_id": request_id}
        return await cls.retrieve_by_tag_filter(context, tag_filter)

    async def attach_invitation(
        self,
        context: InjectionContext,
        invitation: Union[ConnectionInvitation, OOBInvitation],
    ):
        """Persist the related connection invitation to storage.

        Args:
            context: The injection context to use
            invitation: The invitation to relate to this connection record
        """
        assert self.connection_id
        record = StorageRecord(
            self.RECORD_TYPE_INVITATION,
            invitation.to_json(),
            {"connection_id": self.connection_id},
        )
        storage: BaseStorage = await context.inject(BaseStorage)
        await storage.add_record(record)

    async def retrieve_invitation(
        self, context: InjectionContext
    ) -> Union[ConnectionInvitation, OOBInvitation]:
        """Retrieve the related connection invitation.

        Args:
            context: The injection context to use
        """
        assert self.connection_id
        storage: BaseStorage = await context.inject(BaseStorage)
        result = await storage.find_record(
            self.RECORD_TYPE_INVITATION, {"connection_id": self.connection_id}
        )
        ser = json.loads(result.value)
        return (
            ConnectionInvitation
            if DIDCommPrefix.unqualify(ser["@type"]) == CONNECTION_INVITATION
            else OOBInvitation
        ).deserialize(ser)

    async def attach_request(
        self,
        context: InjectionContext,
        request: ConnectionRequest,  # will be Union[ConnectionRequest, DIDEx Request]
    ):
        """Persist the related connection request to storage.

        Args:
            context: The injection context to use
            request: The request to relate to this connection record
        """
        assert self.connection_id
        record = StorageRecord(
            self.RECORD_TYPE_REQUEST,
            request.to_json(),
            {"connection_id": self.connection_id},
        )
        storage: BaseStorage = await context.inject(BaseStorage)
        await storage.add_record(record)

    async def retrieve_request(
        self,
        context: InjectionContext,
    ) -> ConnectionRequest:  # will be Union[ConnectionRequest, DIDEx Request]
        """Retrieve the related connection invitation.

        Args:
            context: The injection context to use
        """
        assert self.connection_id
        storage: BaseStorage = await context.inject(BaseStorage)
        result = await storage.find_record(
            self.RECORD_TYPE_REQUEST, {"connection_id": self.connection_id}
        )
        return ConnectionRequest.from_json(result.value)

    @property
    def is_ready(self) -> str:
        """Accessor for connection readiness."""
        return ConnRecord.State.get(self.state) in (
            ConnRecord.State.COMPLETED,
            ConnRecord.State.RESPONSE,
        )

    @property
    def is_multiuse_invitation(self) -> bool:
        """Accessor for multi use invitation mode."""
        return self.invitation_mode == self.INVITATION_MODE_MULTI

    async def post_save(self, context: InjectionContext, *args, **kwargs):
        """Perform post-save actions.

        Args:
            context: The injection context to use
        """
        await super().post_save(context, *args, **kwargs)

        # clear cache key set by connection manager
        cache_key = self.cache_key(self.connection_id, "connection_target")
        await self.clear_cached_key(context, cache_key)

    def __eq__(self, other: Any) -> bool:
        """Comparison between records."""
        return super().__eq__(other)


class ConnRecordSchema(BaseRecordSchema):
    """Schema to allow serialization/deserialization of connection records."""

    class Meta:
        """ConnRecordSchema metadata."""

        model_class = ConnRecord

    connection_id = fields.Str(
        required=False, description="Connection identifier", example=UUIDFour.EXAMPLE
    )
    my_did = fields.Str(
        required=False, description="Our DID for connection", **INDY_DID
    )
    their_did = fields.Str(
        required=False, description="Their DID for connection", **INDY_DID
    )
    their_label = fields.Str(
        required=False, description="Their label for connection", example="Bob"
    )
    tx_my_role = fields.List(
        fields.Str(),required=False, description="A List of my transaction related roles (AUTHOR/ENDORSER)"
    )
    tx_their_role = fields.List(
        fields.Str(),required=False, description="A List of their transaction related roles (AUTHOR/ENDORSER)"
    )
    their_role = fields.Str(
        required=False,
        description="Their role in the connection protocol",
        validate=validate.OneOf(
            [label for role in ConnRecord.Role for label in role.value]
        ),
        example=ConnRecord.Role.REQUESTER.rfc23,
    )
    inbound_connection_id = fields.Str(
        required=False,
        description="Inbound routing connection id to use",
        example=UUIDFour.EXAMPLE,
    )
    invitation_key = fields.Str(
        required=False, description="Public key for connection", **INDY_RAW_PUBLIC_KEY
    )
    request_id = fields.Str(
        required=False,
        description="Connection request identifier",
        example=UUIDFour.EXAMPLE,
    )
    routing_state = fields.Str(
        required=False,
        description="Routing state of connection",
        validate=validate.OneOf(
            [
                getattr(ConnRecord, m)
                for m in vars(ConnRecord)
                if m.startswith("ROUTING_STATE_")
            ]
        ),
        example=ConnRecord.ROUTING_STATE_ACTIVE,
    )
    accept = fields.Str(
        required=False,
        description="Connection acceptance: manual or auto",
        example=ConnRecord.ACCEPT_AUTO,
        validate=validate.OneOf(
            [
                getattr(ConnRecord, a)
                for a in vars(ConnRecord)
                if a.startswith("ACCEPT_")
            ]
        ),
    )
    error_msg = fields.Str(
        required=False,
        description="Error message",
        example="No DIDDoc provided; cannot connect to public DID",
    )
    invitation_mode = fields.Str(
        required=False,
        description="Invitation mode",
        example=ConnRecord.INVITATION_MODE_ONCE,
        validate=validate.OneOf(
            [
                getattr(ConnRecord, i)
                for i in vars(ConnRecord)
                if i.startswith("INVITATION_MODE_")
            ]
        ),
    )
    alias = fields.Str(
        required=False,
        description="Optional alias to apply to connection for later use",
        example="Bob, providing quotes",
    )

