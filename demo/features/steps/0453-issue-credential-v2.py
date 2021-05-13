from behave import *
import json
from bdd_support.agent_backchannel_client import (
    aries_container_create_schema_cred_def,
    aries_container_issue_credential,
    aries_container_receive_credential,
    read_schema_data,
    read_credential_data,
    agent_container_GET,
    agent_container_POST,
    async_sleep,
)
from bdd_support.agent_test_utils import format_cred_proposal_by_aip_version
from time import sleep

CRED_FORMAT_INDY = "indy"
CRED_FORMAT_JSON_LD = "json-ld"

@given('"{issuer}" is ready to issue a "{cred_format}" credential')
def step_impl(context, issuer: str, cred_format: str = CRED_FORMAT_INDY):
    if cred_format == CRED_FORMAT_INDY:
        # Call legacy indy ready to issue credential step
        context.execute_steps(f'''
            Given "{issuer}" is ready to issue a credential
        ''')
    elif cred_format == CRED_FORMAT_JSON_LD:
        issuer_url = context.config.userdata.get(issuer)

        data = {
            "did_method": context.did_method,
            "proof_type": context.proof_type
        }

        (resp_status, resp_text) = agent_container_POST(issuer_url + "/agent/command/", "issue-credential-v2", operation="prepare-json-ld", data=data)

        assert resp_status == 200, f'issue-credential-v2/prepare-json-ld: resp_status {resp_status} is not 200; {resp_text}'
        resp_json = json.loads(resp_text)

        # TODO: it would be nice to not depend on the schema name for the issuer did dict
        if 'issuer_did_dict' in context:
            context.issuer_did_dict[context.schema['schema_name']] = resp_json["did"]
        else:
            context.issuer_did_dict = {context.schema['schema_name']: resp_json["did"]}
    else:
        raise Exception(f"Unknown credential format {cred_format}")

@given('"{issuer}" offers the "{cred_format}" credential with data {credential_data}')
@when('"{issuer}" offers the "{cred_format}" credential with data {credential_data}')
def step_impl(context, issuer, cred_format):
    issuer_url = context.config.userdata.get(issuer)

    if "credential_data" in context:
        cred_data = context.credential_data
    else:
        try:
            credential_data_json_file = open('features/data/cred_data_' + schema.lower() + '.json')
            credential_data_json = json.load(credential_data_json_file)
        except FileNotFoundError:
            print(FileNotFoundError + ': features/data/cred_data_' + schema.lower() + '.json')

        if 'credential_data_dict' in context:
            context.credential_data_dict[schema] = credential_data_json[credential_data]['attributes']
        else:
            context.credential_data_dict = {schema: credential_data_json[credential_data]['attributes']}

        if "AIP20" in context.tags:
            if 'filters_dict' in context:
                context.filters_dict[schema] = credential_data_json[credential_data]['filters']
            else:
                context.filters_dict = {schema: credential_data_json[credential_data]['filters']}

        context.credential_data = context.credential_data_dict[schema]
        cred_data = context.credential_data

    # We only want to send data for the cred format being used
    assert cred_format in context.filters, f"credential data has no filter for cred format {cred_format}"
    filters = {
        cred_format: context.filters[cred_format]
    }

    credential_offer = format_cred_proposal_by_aip_version(context, "AIP20", cred_data, context.connection_id_dict[issuer][context.holder_name], filters)

    (resp_status, resp_text) = agent_container_POST(issuer_url + "/agent/command/", "issue-credential-v2", operation="send-offer", data=credential_offer)
    assert resp_status == 200, f'resp_status {resp_status} is not 200; {resp_text}'
    resp_json = json.loads(resp_text)
    context.cred_thread_id = resp_json["thread_id"]

    # Check the issuers State
    assert resp_json["state"] == "offer-sent"

    # Check the state of the holder after issuers call of send-offer
    #assert expected_agent_state(context.holder_url, "issue-credential-v2", context.cred_thread_id, "offer-received")


@when('"{holder}" requests the "{cred_format}" credential')
def step_impl(context, holder, cred_format):
    holder_url = context.holder_url

    # # If @indy then we can be sure we cannot start the protocol from this command. We can be sure that we have previously 
    # # reveived the thread_id.
    # if "Indy" in context.tags:
    #     sleep(1)
    (resp_status, resp_text) = agent_container_POST(holder_url + "/agent/command/", "issue-credential-v2", operation="send-request", id=context.cred_thread_id)

    # # If we are starting from here in the protocol you won't have the cred_ex_id or the thread_id
    # else:
    #     (resp_status, resp_text) = agent_container_POST(holder_url + "/agent/command/", "issue-credential-v2", operation="send-request", id=context.connection_id_dict[holder][context.issuer_name])
    
    assert resp_status == 200, f'resp_status {resp_status} is not 200; {resp_text}'
    resp_json = json.loads(resp_text)
    assert resp_json["state"] == "request-sent"

    # Verify issuer status
    #assert expected_agent_state(context.issuer_url, "issue-credential-v2", context.cred_thread_id, "request-received")


@when('"{issuer}" issues the "{cred_format}" credential')
def step_impl(context, issuer, cred_format):
    issuer_url = context.config.userdata.get(issuer)

    credential_issue = {
        "comment": "issuing credential"
    }

    (resp_status, resp_text) = agent_container_POST(issuer_url + "/agent/command/", "issue-credential-v2", operation="issue", id=context.cred_thread_id, data=credential_issue)
    assert resp_status == 200, f'resp_status {resp_status} is not 200; {resp_text}'
    resp_json = json.loads(resp_text)
    assert resp_json["state"] == "credential-issued"

    # Verify holder status
    #assert expected_agent_state(context.holder_url, "issue-credential-v2", context.cred_thread_id, "credential-received")


@when('"{holder}" acknowledges the "{cred_format}" credential issue')
def step_impl(context, holder, cred_format):
    holder_url = context.config.userdata.get(holder)
    
    # a credential id shouldn't be needed with a cred_ex_id being passed
    # credential_id = {
    #     "credential_id": context.cred_thread_id,
    # }
    credential_id = {
        "comment": "storing credential"
    }

    sleep(1)
    (resp_status, resp_text) = agent_container_POST(holder_url + "/agent/command/", "issue-credential-v2", operation="store", id=context.cred_thread_id, data=credential_id)
    assert resp_status == 200, f'resp_status {resp_status} is not 200; {resp_text}'
    resp_json = json.loads(resp_text)
    assert resp_json["state"] == "done"

    credential_id = resp_json[cred_format]["credential_id"]
    # credential_id = resp_json["cred_ex_record"]["cred_id_stored"]

    if 'credential_id_dict' in context:
        try:
            context.credential_id_dict[context.schema['schema_name']].append(credential_id)
        except KeyError:
            context.credential_id_dict[context.schema['schema_name']] = [credential_id]
    else:
        context.credential_id_dict = {context.schema['schema_name']: [credential_id]}

    # Verify issuer status
    # TODO This is returning none instead of Done. Should this be the case. Needs investigation.
    #assert expected_agent_state(context.issuer_url, "issue-credential-v2", context.cred_thread_id, "done")

    # if the credential supports revocation, get the Issuers webhook callback JSON from the store command
    # From that JSON save off the credential revocation identifier, and the revocation registry identifier.
    if "support_revocation" in context:
        if context.support_revocation:
            (resp_status, resp_text) = agent_container_GET(context.config.userdata.get(context.issuer_name) + "/agent/response/", "revocation-registry", id=context.cred_thread_id)
            assert resp_status == 200, f'resp_status {resp_status} is not 200; {resp_text}'
            resp_json = json.loads(resp_text)
            context.cred_rev_id = resp_json["revocation_id"]
            context.rev_reg_id = resp_json["revoc_reg_id"]


@then('"{holder}" has the "{cred_format}" credential issued')
def step_impl(context, holder, cred_format):
    holder_url = context.config.userdata.get(holder)

    # get the credential from the holders wallet
    (resp_status, resp_text) = agent_container_GET(holder_url + "/agent/command/", "credential", id=context.credential_id_dict[context.schema['schema_name']][-1])
    assert resp_status == 200, f'resp_status {resp_status} is not 200; {resp_text}'
    resp_json = json.loads(resp_text)

    if cred_format == CRED_FORMAT_INDY:
        assert resp_json["schema_id"] == context.issuer_schema_id_dict[context.schema["schema_name"]]
        assert resp_json["cred_def_id"] == context.credential_definition_id_dict[context.schema["schema_name"]]
        assert resp_json["referent"] == context.credential_id_dict[context.schema['schema_name']][-1]
    elif cred_format == CRED_FORMAT_JSON_LD:
        # TODO: do not use schema name for credential_id_dict
        assert resp_json["credential_id"] == context.credential_id_dict[context.schema['schema_name']][-1]

