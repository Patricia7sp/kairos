"""Sessões explícitas e recibos pessoais para fixtures de serviços e journals."""

import json
import uuid
from functools import wraps

from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext, OrgPolicyReceipt


def personal_policy(test, home):
    context = observe_org_policy(OrgPolicyContext("none", None), home)
    if hasattr(test, "enterContext"):
        return test.enterContext(context)
    session = context.__enter__()
    test.addfinalizer(lambda: context.__exit__(None, None, None))
    return session


def personal_receipt(draft, repository=None):
    if draft.action.value not in ("create", "restore"):
        return None
    if repository is not None:
        from kairos_state.repositories.skill_org_protections import has_org_schema

        if not has_org_schema(repository._conn):
            return None
    return OrgPolicyReceipt(draft.home_id, draft.name, uuid.uuid4().hex, None, ())


def personal_operation(function):
    """Processos de fixture mantêm a observação aberta por toda a chamada."""

    @wraps(function)
    def operation(home, *args, **kwargs):
        with observe_org_policy(OrgPolicyContext("none", None), home) as policy:
            return function(home, *args, policy=policy, **kwargs)

    return operation


def personal_sync_operation(function):
    @wraps(function)
    def operation(bundle, home, *args, **kwargs):
        with observe_org_policy(OrgPolicyContext("none", None), home) as policy:
            return function(bundle, home, *args, policy=policy, **kwargs)

    return operation


def bound_policy(test, home, source, *, name="org-skill", active=False):
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": [
                    {
                        "home": str(home),
                        "name": name,
                        "organization_id": "private-organization-sentinel",
                        "active": active,
                    }
                ],
            }
        )
    )
    return test.enterContext(observe_org_policy(OrgPolicyContext("file", source), home))
