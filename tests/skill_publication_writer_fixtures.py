"""Composição de pacote confiável explícita nas fixtures de sync."""

import uuid
from unittest.mock import patch

from kairos_domain.ownership import Actor
from kairos_skills.acquisitions import SkillAcquisitionService
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_bundled_source, observe_quarantine_source
from kairos_skills.sync import sync_bundled_skills as publish_bundled


def sync_bundled_skills(bundled_dir, user_dir, *, policy):
    with (
        patch("kairos_skills.publication_sources.trusted_bundle_root", return_value=bundled_dir),
        observe_bundled_source(user_dir.parent, policy=policy) as context,
    ):
        return publish_bundled(bundled_dir, user_dir, policy=policy, context=context)


def acquired_source(test, home, quarantine, bundle, *, policy, replacement=None):
    quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
    context = test.enterContext(
        observe_quarantine_source(
            home,
            quarantine,
            source_key=uuid.uuid4().hex,
            actor=Actor.USER_FOREGROUND,
            policy=policy,
            replacement=replacement,
        )
    )
    with publication_session(home, policy=policy, context=context) as session:
        receipt = SkillAcquisitionService(session).acquire(bundle)
    return quarantine / receipt.acquisition_id / bundle.name, context, receipt.acquisition_id


def descriptor_budget_worker(home, bundle, pipe, disable_release=False):
    import resource
    from contextlib import nullcontext

    from kairos_skills.mutation_contract import SkillMutationError
    from kairos_skills.org_policy import observe_org_policy
    from kairos_skills.org_policy_contract import OrgPolicyContext
    from kairos_skills.publication_sources import PublicationContext

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(160, soft), hard))
    try:
        with (
            observe_org_policy(OrgPolicyContext("none", None), home) as policy,
            (
                patch.object(PublicationContext, "_release_completed_bundle")
                if disable_release
                else nullcontext()
            ),
        ):
            result = sync_bundled_skills(bundle, home / "skills", policy=policy)
            repeated = sync_bundled_skills(bundle, home / "skills", policy=policy)
            pipe.send(
                {
                    "failed": result.failed or repeated.failed,
                    "copied": len(result.copied),
                    "skipped": len(repeated.skipped),
                    "complete": all(item.complete for item in result.publications),
                }
            )
    except SkillMutationError as error:
        pipe.send({"failed": True, "error_kind": error.kind})
    finally:
        pipe.close()
