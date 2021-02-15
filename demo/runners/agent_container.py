import asyncio
import json
import logging
import os
import random
import sys
import time

from qrcode import QRCode

from aiohttp import ClientError

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runners.support.agent import (  # noqa:E402
    DemoAgent,
    default_genesis_txns,
    start_mediator_agent,
    connect_wallet_to_mediator,
)
from runners.support.utils import (  # noqa:E402
    log_msg,
    log_status,
    log_timer,
    prompt,
    prompt_loop,
    require_indy,
)


CRED_PREVIEW_TYPE = "https://didcomm.org/issue-credential/2.0/credential-preview"
SELF_ATTESTED = os.getenv("SELF_ATTESTED")
TAILS_FILE_COUNT = int(os.getenv("TAILS_FILE_COUNT", 100))

logging.basicConfig(level=logging.WARNING)
LOGGER = logging.getLogger(__name__)


class AriesAgent(DemoAgent):
    def __init__(
        self,
        ident: str,
        http_port: int,
        admin_port: int,
        no_auto: bool = False,
        **kwargs,
    ):
        super().__init__(
            ident,
            http_port,
            admin_port,
            prefix="Aries",
            extra_args=[]
            if no_auto
            else ["--auto-accept-invites", "--auto-accept-requests"],
            **kwargs,
        )
        self.connection_id = None
        self._connection_ready = None
        self.cred_state = {}
        # TODO define a dict to hold credential attributes
        # based on cred_def_id
        self.cred_attrs = {}

    async def detect_connection(self):
        await self._connection_ready
        self._connection_ready = None

    @property
    def connection_ready(self):
        return self._connection_ready.done() and self._connection_ready.result()

    async def handle_oob_invitation(self, message):
        pass

    async def handle_connections(self, message):
        # a bit of a hack, but for the mediator connection self._connection_ready
        # will be None
        if not self._connection_ready:
            return

        conn_id = message["connection_id"]
        if message["state"] == "invitation":
            self.connection_id = conn_id
        if conn_id == self.connection_id:
            if (
                message["rfc23_state"] in ["completed", "response-sent"]
                and not self._connection_ready.done()
            ):
                self.log("Connected")
                self._connection_ready.set_result(True)

    async def handle_issue_credential_v2_0(self, message):
        state = message["state"]
        cred_ex_id = message["cred_ex_id"]
        prev_state = self.cred_state.get(cred_ex_id)
        if prev_state == state:
            return  # ignore
        self.cred_state[cred_ex_id] = state

        self.log(f"Credential: state = {state}, cred_ex_id = {cred_ex_id}")

        if state == "request-received":
            log_status("#17 Issue credential to X")
            # issue credential based on offer preview in cred ex record
            await self.admin_POST(
                f"/issue-credential-2.0/records/{cred_ex_id}/issue",
                {"comment": f"Issuing credential, exchange {cred_ex_id}"},
            )

    async def handle_issue_credential_v2_0_indy(self, message):
        rev_reg_id = message.get("rev_reg_id")
        cred_rev_id = message.get("cred_rev_id")
        if rev_reg_id and cred_rev_id:
            self.log(f"Revocation registry ID: {rev_reg_id}")
            self.log(f"Credential revocation ID: {cred_rev_id}")

    async def handle_issuer_cred_rev(self, message):
        pass

    async def handle_present_proof(self, message):
        state = message["state"]

        pres_ex_id = message["presentation_exchange_id"]
        self.log(f"Presentation: state = {state}, pres_ex_id = {pres_ex_id}")

        if state == "presentation_received":
            log_status("#27 Process the proof provided by X")
            log_status("#28 Check if proof is valid")
            proof = await self.admin_POST(
                f"/present-proof/records/{pres_ex_id}/verify-presentation"
            )
            self.log("Proof =", proof["verified"])

    async def handle_basicmessages(self, message):
        self.log("Received message:", message["content"])

    async def generate_invitation(self, use_did_exchange: bool, auto_accept: bool = True, display_qr: bool = False, wait: bool = False):
        self._connection_ready = asyncio.Future()
        with log_timer("Generate invitation duration:"):
            # Generate an invitation
            log_status("#7 Create a connection to alice and print out the invite details")
            invi_rec = await self.get_invite(use_did_exchange, auto_accept)

        if display_qr:
            qr = QRCode(border=1)
            qr.add_data(invi_rec["invitation_url"])
            log_msg(
                "Use the following JSON to accept the invite from another demo agent."
                " Or use the QR code to connect from a mobile agent."
            )
            log_msg(json.dumps(invi_rec["invitation"]), label="Invitation Data:", color=None)
            qr.print_ascii(invert=True)

        if wait:
            log_msg("Waiting for connection...")
            await self.detect_connection()

        return invi_rec

    async def create_schema_and_cred_def(self, schema_name, schema_attrs, revocation):
        with log_timer("Publish schema/cred def duration:"):
            log_status("#3/4 Create a new schema/cred def on the ledger")
            version = format(
                "%d.%d.%d"
                % (
                    random.randint(1, 101),
                    random.randint(1, 101),
                    random.randint(1, 101),
                )
            )
            (_, cred_def_id,) = await self.register_schema_and_creddef(  # schema id
                schema_name,
                version,
                schema_attrs,
                support_revocation=revocation,
                revocation_registry_size=TAILS_FILE_COUNT if revocation else None,
            )
            return cred_def_id


class AgentContainer():
    def __init__(
        self,
        genesis_txns: str,
        ident: str,
        start_port: int,
        no_auto: bool = False,
        revocation: bool = False,
        tails_server_base_url: str = None,
        show_timing: bool = False,
        multitenant: bool = False,
        mediation: bool = False,
        use_did_exchange: bool = False,
        wallet_type: str = None,
        seed: str = "random",
    ):
        # configuration parameters
        self.genesis_txns = genesis_txns
        self.ident = ident
        self.start_port = start_port
        self.no_auto = no_auto
        self.revocation = revocation
        self.tails_server_base_url = tails_server_base_url
        self.show_timing = show_timing
        self.multitenant = multitenant
        self.mediation = mediation
        self.use_did_exchange = use_did_exchange
        self.wallet_type = wallet_type
        self.seed = seed

        # local agent(s)
        self.agent = None
        self.mediator_agent = None

    async def initialize(
        self,
        the_agent: DemoAgent = None,
        public_did: bool = False,
        schema_name: str = None,
        schema_attrs: list = None,
    ):
        """Startup agent(s), register DID, schema, cred def as appropriate."""

        if not the_agent:
            log_status(
                "#1 Provision an agent and wallet, get back configuration details"
                + (f" (Wallet type: {self.wallet_type})" if self.wallet_type else "")
            )
            self.agent = AriesAgent(
                self.ident,
                self.start_port,
                self.start_port + 1,
                genesis_data = self.genesis_txns,
                no_auto = self.no_auto,
                tails_server_base_url = self.tails_server_base_url,
                timing = self.show_timing,
                revocation = self.revocation,
                multitenant = self.multitenant,
                mediation = self.mediation,
                wallet_type = self.wallet_type,
                seed = self.seed,
            )
        else:
            self.agent = the_agent

        await self.agent.listen_webhooks(self.start_port + 2)

        if public_did:
            await self.agent.register_did()

        with log_timer("Startup duration:"):
            await self.agent.start_process()

        log_msg("Admin URL is at:", self.agent.admin_url)
        log_msg("Endpoint URL is at:", self.agent.endpoint)

        if self.mediation:
            self.mediator_agent = await start_mediator_agent(self.start_port + 4, self.genesis_txns)
            if not self.mediator_agent:
                raise Exception("Mediator agent returns None :-(")
        else:
            self.mediator_agent = None

        if self.multitenant:
            # create an initial managed sub-wallet (also mediated)
            await self.agent.register_or_switch_wallet(
                self.ident + ".initial",
                public_did=public_did,
                webhook_port=self.agent.get_new_webhook_port(),
                mediator_agent=self.mediator_agent,
            )
        elif self.mediation:
            # we need to pre-connect the agent to its mediator
            if not await connect_wallet_to_mediator(self.agent, self.mediator_agent):
                raise Exception("Mediation setup FAILED :-(")

        if schema_name and schema_attrs:
            if not public_did:
                raise Exception("Can't create a schema/cred def without a public DID :-(")
            # Create a schema
            self.cred_def_id = await self.agent.create_schema_and_cred_def(schema_name, schema_attrs, self.revocation)

    async def terminate(self):
        """Shut down any running agents."""

        terminated = True
        try:
            if self.mediator_agent:
                log_msg("Shutting down mediator agent ...")
                await self.mediator_agent.terminate()
            if self.agent:
                log_msg("Shutting down agent ...")
                await self.agent.terminate()
        except Exception:
            LOGGER.exception("Error terminating agent:")
            terminated = False

        return terminated


async def test_main(
    start_port: int,
    no_auto: bool = False,
    revocation: bool = False,
    tails_server_base_url: str = None,
    show_timing: bool = False,
    multitenant: bool = False,
    mediation: bool = False,
    use_did_exchange: bool = False,
    wallet_type: str = None,
):
    """Test to startup a couple of agents."""

    genesis = await default_genesis_txns()
    if not genesis:
        print("Error retrieving ledger genesis transactions")
        sys.exit(1)

    faber_container = None
    alice_container = None
    try:
        # initialize the containers
        faber_container = AgentContainer(
            genesis,
            "Faber.agent",
            start_port,
            no_auto = no_auto,
            revocation = revocation,
            tails_server_base_url = tails_server_base_url,
            show_timing = show_timing,
            multitenant = multitenant,
            mediation = mediation,
            use_did_exchange = use_did_exchange,
            wallet_type = wallet_type,
        )
        alice_container = AgentContainer(
            genesis,
            "Alice.agent",
            start_port+10,
            no_auto = no_auto,
            revocation = False,
            show_timing = show_timing,
            multitenant = multitenant,
            mediation = mediation,
            use_did_exchange = use_did_exchange,
            wallet_type = wallet_type,
            seed = None,
        )

        # start the agents - faber gets a public DID and schema/cred def
        await faber_container.initialize(
            public_did = True,
            schema_name = "degree schema",
            schema_attrs = ["name", "date", "degree", "grade",],
        )
        await alice_container.initialize(
            public_did = False,
        )

        # TODO faber create invitation
        # TODO alice accept invitation
        # TODO wait for faber connection to activate
        # TODO faber issue credential to alice
        # TODO alice check for received credential

        log_msg("Sleeping ...")
        await asyncio.sleep(3.0)

    except Exception as e:
            LOGGER.exception("Error initializing agent:", e)
            raise(e)

    finally:
        terminated = True
        try:
            # shut down containers at the end of the test
            if alice_container:
                log_msg("Shutting down alice agent ...")
                await alice_container.terminate()
            if faber_container:
                log_msg("Shutting down faber agent ...")
                await faber_container.terminate()
        except Exception as e:
            LOGGER.exception("Error terminating agent:", e)
            terminated = False

    await asyncio.sleep(0.1)

    if not terminated:
        os._exit(1)

    await asyncio.sleep(2.0)
    os._exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Runs an Aries demo agent.")
    parser.add_argument("--no-auto", action="store_true", help="Disable auto issuance")
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=8020,
        metavar=("<port>"),
        help="Choose the starting port number to listen on",
    )
    parser.add_argument(
        "--did-exchange",
        action="store_true",
        help="Use DID-Exchange protocol for connections",
    )
    parser.add_argument(
        "--revocation", action="store_true", help="Enable credential revocation"
    )
    parser.add_argument(
        "--tails-server-base-url",
        type=str,
        metavar=("<tails-server-base-url>"),
        help="Tals server base url",
    )
    parser.add_argument(
        "--timing", action="store_true", help="Enable timing information"
    )
    parser.add_argument(
        "--multitenant", action="store_true", help="Enable multitenancy options"
    )
    parser.add_argument(
        "--mediation", action="store_true", help="Enable mediation functionality"
    )
    parser.add_argument(
        "--wallet-type",
        type=str,
        metavar="<wallet-type>",
        help="Set the agent wallet type",
    )
    args = parser.parse_args()

    if args.did_exchange and args.mediation:
        raise Exception(
            "DID-Exchange connection protocol is not (yet) compatible with mediation"
        )

    ENABLE_PYDEVD_PYCHARM = os.getenv("ENABLE_PYDEVD_PYCHARM", "").lower()
    ENABLE_PYDEVD_PYCHARM = ENABLE_PYDEVD_PYCHARM and ENABLE_PYDEVD_PYCHARM not in (
        "false",
        "0",
    )
    PYDEVD_PYCHARM_HOST = os.getenv("PYDEVD_PYCHARM_HOST", "localhost")
    PYDEVD_PYCHARM_CONTROLLER_PORT = int(
        os.getenv("PYDEVD_PYCHARM_CONTROLLER_PORT", 5001)
    )

    if ENABLE_PYDEVD_PYCHARM:
        try:
            import pydevd_pycharm

            print(
                "Aries aca-py remote debugging to "
                f"{PYDEVD_PYCHARM_HOST}:{PYDEVD_PYCHARM_CONTROLLER_PORT}"
            )
            pydevd_pycharm.settrace(
                host=PYDEVD_PYCHARM_HOST,
                port=PYDEVD_PYCHARM_CONTROLLER_PORT,
                stdoutToServer=True,
                stderrToServer=True,
                suspend=False,
            )
        except ImportError:
            print("pydevd_pycharm library was not found")

    require_indy()

    tails_server_base_url = args.tails_server_base_url or os.getenv("PUBLIC_TAILS_URL")

    if args.revocation and not tails_server_base_url:
        raise Exception(
            "If revocation is enabled, --tails-server-base-url must be provided"
        )

    try:
        asyncio.get_event_loop().run_until_complete(
            test_main(
                args.port,
                args.no_auto,
                args.revocation,
                tails_server_base_url,
                args.timing,
                args.multitenant,
                args.mediation,
                args.did_exchange,
                args.wallet_type,
            )
        )
    except KeyboardInterrupt:
        os._exit(1)