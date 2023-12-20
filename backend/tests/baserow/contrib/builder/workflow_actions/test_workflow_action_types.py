from collections import defaultdict

import pytest

from baserow.contrib.builder.workflow_actions.models import EventTypes
from baserow.contrib.builder.workflow_actions.registries import (
    builder_workflow_action_type_registry,
)
from baserow.contrib.builder.workflow_actions.workflow_action_types import (
    UpsertRowWorkflowActionType,
)
from baserow.core.utils import MirrorDict
from baserow.core.workflow_actions.registries import WorkflowActionType


def pytest_generate_tests(metafunc):
    if "workflow_action_type" in metafunc.fixturenames:
        metafunc.parametrize(
            "workflow_action_type",
            [
                pytest.param(e, id=e.type)
                for e in builder_workflow_action_type_registry.get_all()
            ],
        )


def fake_import_formula(formula, id_mapping):
    return formula


@pytest.mark.django_db
def test_export_workflow_action(data_fixture, workflow_action_type: WorkflowActionType):
    page = data_fixture.create_builder_page()
    pytest_params = workflow_action_type.get_pytest_params(data_fixture)
    workflow_action = data_fixture.create_workflow_action(
        workflow_action_type.model_class, page=page, **pytest_params
    )

    exported = workflow_action_type.export_serialized(workflow_action)

    assert exported["id"] == workflow_action.id
    assert exported["type"] == workflow_action_type.type

    serialized_pytest_params = workflow_action_type.get_pytest_params_serialized(
        pytest_params
    )
    for key, value in serialized_pytest_params.items():
        assert exported[key] == value


@pytest.mark.django_db
def test_import_workflow_action(data_fixture, workflow_action_type: WorkflowActionType):
    page = data_fixture.create_builder_page()
    pytest_params = workflow_action_type.get_pytest_params(data_fixture)

    page_after_import = data_fixture.create_builder_page()

    serialized = {
        "id": 9999,
        "type": workflow_action_type.type,
        "page_id": page.id,
        "order": 0,
    }
    serialized.update(workflow_action_type.get_pytest_params_serialized(pytest_params))

    id_mapping = defaultdict(lambda: MirrorDict())
    id_mapping["builder_pages"] = {page.id: page_after_import.id}
    workflow_action = workflow_action_type.import_serialized(
        page, serialized, id_mapping
    )

    assert workflow_action.id != 9999
    assert isinstance(workflow_action, workflow_action_type.model_class)

    if not issubclass(workflow_action_type.__class__, UpsertRowWorkflowActionType):
        for key, value in pytest_params.items():
            assert getattr(workflow_action, key) == value


@pytest.mark.django_db
def test_export_import_upsert_row_workflow_action_type(data_fixture):
    user, token = data_fixture.create_user_and_token()
    page = data_fixture.create_builder_page(user=user)
    table, fields, rows = data_fixture.build_table(
        user=user,
        columns=[
            ("Animal", "text"),
        ],
        rows=[],
    )
    integration = data_fixture.create_local_baserow_integration(user=user)
    data_source = data_fixture.create_builder_local_baserow_list_rows_data_source(
        table=table, page=page
    )
    field = table.field_set.get(name="Animal")
    element = data_fixture.create_builder_button_element(page=page)
    service = data_fixture.create_local_baserow_upsert_row_service(
        integration=integration, table=table
    )
    field_mapping = service.field_mappings.create(
        field=field, value=f"get('data_source.{data_source.id}.{field.db_column}')"
    )
    workflow_action = data_fixture.create_local_baserow_create_row_workflow_action(
        page=page, element=element, event=EventTypes.CLICK, service=service
    )

    workflow_action_type = workflow_action.get_type()
    exported = workflow_action_type.export_serialized(workflow_action)

    assert exported == {
        "id": workflow_action.id,
        "order": workflow_action.order,
        "type": workflow_action_type.type,
        "page_id": page.id,
        "element_id": element.id,
        "event": EventTypes.CLICK,
        "service": {
            "id": service.id,
            "integration_id": integration.id,
            "type": "local_baserow_upsert_row",
            "row_id": "",
            "table_id": table.id,
            "field_mappings": [{"field_id": field.id, "value": field_mapping.value}],
        },
    }

    id_mapping = defaultdict(lambda: MirrorDict())
    new_workflow_action = workflow_action_type.import_serialized(
        page, exported, id_mapping
    )
    new_action_service = new_workflow_action.service

    assert new_workflow_action.id != exported["id"]
    assert new_workflow_action.event == exported["event"]
    assert new_workflow_action.page_id == exported["page_id"]
    assert new_workflow_action.element_id == exported["element_id"]

    assert new_workflow_action.service_id != exported["service"]["id"]
    assert new_action_service.row_id == exported["service"]["row_id"]
    assert new_action_service.table_id == exported["service"]["table_id"]
    assert new_action_service.integration_id == exported["service"]["integration_id"]

    field_mapping = service.field_mappings.get()
    assert (
        field_mapping.field_id == exported["service"]["field_mappings"][0]["field_id"]
    )
    assert field_mapping.value == exported["service"]["field_mappings"][0]["value"]


@pytest.mark.django_db
def test_upsert_row_workflow_action_prepare_values_with_instance(
    data_fixture,
):
    user, token = data_fixture.create_user_and_token()
    page = data_fixture.create_builder_page(user=user)
    workspace = page.builder.workspace
    element = data_fixture.create_builder_button_element(page=page)
    integration = data_fixture.create_local_baserow_integration(
        application=page.builder
    )
    database = data_fixture.create_database_application(workspace=workspace)
    table = data_fixture.create_database_table(database=database)
    workflow_action = data_fixture.create_local_baserow_create_row_workflow_action(
        page=page, element=element, event=EventTypes.CLICK, user=user
    )
    service = workflow_action.service.specific
    service.table = table
    service.save()
    field = data_fixture.create_text_field(table=table)
    model = table.get_model()
    row2 = model.objects.create(**{f"field_{field.id}": "Cheese"})
    UpsertRowWorkflowActionType().prepare_values(
        {
            "service": {
                "row_id": row2.id,
                "table_id": table.id,
                "integration_id": integration.id,
            }
        },
        user,
        workflow_action,
    )
    assert service.row_id == row2.id
    assert service.table_id == table.id
    assert service.integration_id == integration.id
