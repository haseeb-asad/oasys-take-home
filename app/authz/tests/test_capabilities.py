"""Unit tests for the authz capability vocabulary and role grants."""

import pytest

from app.authz import Capability, GrantRole, capabilities_for

EXPECTED_ROLE_CAPABILITIES: dict[GrantRole, frozenset[Capability]] = {
    GrantRole.PHYSICIAN: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
            Capability.VIEW_REHAB_ASSESSMENT,
            Capability.VIEW_CLINICAL,
            Capability.WRITE_CLINICAL,
            Capability.BILL,
        }
    ),
    GrantRole.PHYSIOTHERAPIST: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
            Capability.VIEW_REHAB_ASSESSMENT,
            Capability.VIEW_CLINICAL,
            Capability.WRITE_CLINICAL,
            Capability.BILL,
        }
    ),
    GrantRole.PERSONAL_TRAINER: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
        }
    ),
    GrantRole.MASSAGE_THERAPIST: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
        }
    ),
    GrantRole.NUTRITION_COACH: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.RUN_SESSION,
            Capability.MESSAGE_CLIENT,
        }
    ),
    GrantRole.ORG_ADMIN: frozenset(
        {
            Capability.VIEW_BASIC_PROFILE,
            Capability.VIEW_SCHEDULE,
            Capability.BILL,
            Capability.MANAGE_TEAM,
        }
    ),
}


def _holders(capability: Capability) -> set[GrantRole]:
    return {
        role
        for role, capabilities in EXPECTED_ROLE_CAPABILITIES.items()
        if capability in capabilities
    }


def test_capability_vocabulary_is_the_planned_flat_set() -> None:
    assert {capability.value for capability in Capability} == {
        "view_clinical",
        "write_clinical",
        "view_rehab_assessment",
        "run_session",
        "message_client",
        "bill",
        "view_schedule",
        "view_basic_profile",
        "manage_team",
    }


def test_grant_roles_are_provider_roles_plus_org_admin() -> None:
    from app.care.domain.value_objects import Role

    provider_role_values = {role.value for role in Role}

    assert {grant_role.value for grant_role in GrantRole} == provider_role_values | {"org_admin"}
    assert "org_admin" not in provider_role_values


@pytest.mark.parametrize(
    ("role", "expected_capabilities"),
    EXPECTED_ROLE_CAPABILITIES.items(),
)
def test_capabilities_for_role(
    role: GrantRole,
    expected_capabilities: frozenset[Capability],
) -> None:
    assert capabilities_for(role) == expected_capabilities


def test_clinical_and_rehab_access_is_limited_to_physician_and_physio() -> None:
    clinical_roles = {GrantRole.PHYSICIAN, GrantRole.PHYSIOTHERAPIST}

    assert _holders(Capability.VIEW_CLINICAL) == clinical_roles
    assert _holders(Capability.WRITE_CLINICAL) == clinical_roles
    assert _holders(Capability.VIEW_REHAB_ASSESSMENT) == clinical_roles


def test_massage_therapist_can_act_without_clinical_visibility() -> None:
    capabilities = capabilities_for(GrantRole.MASSAGE_THERAPIST)

    assert Capability.RUN_SESSION in capabilities
    assert Capability.MESSAGE_CLIENT in capabilities
    assert Capability.VIEW_CLINICAL not in capabilities
    assert Capability.VIEW_REHAB_ASSESSMENT not in capabilities


def test_manage_team_is_not_a_static_provider_role_grant() -> None:
    provider_roles = set(GrantRole) - {GrantRole.ORG_ADMIN}

    assert _holders(Capability.MANAGE_TEAM) == {GrantRole.ORG_ADMIN}
    for role in provider_roles:
        assert Capability.MANAGE_TEAM not in capabilities_for(role)


def test_capability_rows_are_immutable_and_fail_loud_for_unknown_roles() -> None:
    assert isinstance(capabilities_for(GrantRole.PHYSICIAN), frozenset)

    with pytest.raises(KeyError):
        capabilities_for("not_a_role")  # type: ignore[arg-type]
