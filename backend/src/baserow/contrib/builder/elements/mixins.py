from typing import Any, Dict, List, Optional, Type

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError

from baserow.api.exceptions import RequestBodyValidationException
from baserow.contrib.builder.data_sources.handler import DataSourceHandler
from baserow.contrib.builder.elements.handler import ElementHandler
from baserow.contrib.builder.elements.models import (
    CollectionField,
    ContainerElement,
    Element,
    FormElement,
    LinkElement,
    TableElement,
)
from baserow.contrib.builder.elements.registries import (
    collection_field_type_registry,
    element_type_registry,
)
from baserow.contrib.builder.elements.signals import elements_moved
from baserow.contrib.builder.pages.handler import PageHandler
from baserow.contrib.builder.types import ElementDict


class ContainerElementTypeMixin:
    # Container element types are imported first.
    import_element_priority = 2

    @property
    def child_types_allowed(self) -> List[str]:
        """
        Lets you define which children types can be placed inside the container.

        :return: All the allowed children types
        """

        return [element_type.type for element_type in element_type_registry.get_all()]

    def get_new_place_in_container(
        self, container_element: ContainerElement, places_removed: List[str]
    ) -> Optional[str]:
        """
        Provides an alternative place that elements can move to when places in the
        container are removed.

        :param container_element: The container element that has places removed
        :param places_removed: The places that are being removed
        :return: The new place in the container the elements can be moved to
        """

        return None

    def get_places_in_container_removed(
        self, values: Dict, instance: ContainerElement
    ) -> List[str]:
        """
        This method defines what elements in the container have been removed preceding
        an update of hte container element.

        :param values: The new values that are being set
        :param instance: The current state of the element
        :return: The places in the container that have been removed
        """

        return []

    def apply_order_by_children(self, queryset: QuerySet[Element]) -> QuerySet[Element]:
        """
        Defines the order of the children inside the container.

        :param queryset: The queryset that the order is applied to.
        :return: A queryset with the order applied to
        """

        return queryset.order_by("place_in_container", "order")

    def prepare_value_for_db(
        self, values: Dict, instance: Optional[ContainerElement] = None
    ):
        if instance is not None:  # This is an update operation
            places_removed = self.get_places_in_container_removed(values, instance)

            if len(places_removed) > 0:
                instances_moved = ElementHandler().before_places_in_container_removed(
                    instance, places_removed
                )

                elements_moved.send(self, page=instance.page, elements=instances_moved)

        return super().prepare_value_for_db(values, instance)

    def validate_place_in_container(
        self, place_in_container: str, instance: ContainerElement
    ):
        """
        Validate that the place in container being set on a child is valid.

        :param place_in_container: The place in container being set
        :param instance: The instance of the container element
        :raises DRFValidationError: If the place in container is invalid
        """


class CollectionElementTypeMixin:
    allowed_fields = ["data_source", "data_source_id", "items_per_page"]
    serializer_field_names = ["data_source_id", "fields", "items_per_page"]

    class SerializedDict(ElementDict):
        data_source_id: int
        items_per_page: int
        fields: List[Dict]

    @property
    def serializer_field_overrides(self):
        from baserow.contrib.builder.api.elements.serializers import (
            CollectionFieldSerializer,
        )

        return {
            "data_source_id": serializers.IntegerField(
                allow_null=True,
                default=None,
                help_text=TableElement._meta.get_field("data_source").help_text,
                required=False,
            ),
            "items_per_page": serializers.IntegerField(
                default=20,
                help_text=TableElement._meta.get_field("items_per_page").help_text,
                required=False,
            ),
            "fields": CollectionFieldSerializer(many=True, required=False),
        }

    def prepare_value_for_db(
        self, values: Dict, instance: Optional[LinkElement] = None
    ):
        if "data_source_id" in values:
            data_source_id = values.pop("data_source_id")
            if data_source_id is not None:
                data_source = DataSourceHandler().get_data_source(data_source_id)
                if (
                    not data_source.service
                    or not data_source.service.specific.get_type().returns_list
                ):
                    raise DRFValidationError(
                        f"The data source with ID {data_source_id} doesn't return a "
                        "list."
                    )

                if instance:
                    current_page = PageHandler().get_page(instance.page_id)
                else:
                    current_page = values["page"]

                if current_page.id != data_source.page_id:
                    raise RequestBodyValidationException(
                        {
                            "data_source_id": [
                                {
                                    "detail": "The provided data source doesn't belong "
                                    "to the same application.",
                                    "code": "invalid_data_source",
                                }
                            ]
                        }
                    )
                values["data_source"] = data_source
            else:
                values["data_source"] = None

        return super().prepare_value_for_db(values, instance)

    def after_create(self, instance, values):
        default_fields = [
            {
                "name": _("Column %(count)s") % {"count": 1},
                "type": "text",
                "config": {"value": ""},
            },
            {
                "name": _("Column %(count)s") % {"count": 2},
                "type": "text",
                "config": {"value": ""},
            },
            {
                "name": _("Column %(count)s") % {"count": 3},
                "type": "text",
                "config": {"value": ""},
            },
        ]

        fields = values.get("fields", default_fields)

        created_fields = CollectionField.objects.bulk_create(
            [
                CollectionField(**field, order=index)
                for index, field in enumerate(fields)
            ]
        )
        instance.fields.add(*created_fields)

    def after_update(self, instance, values):
        if "fields" in values:
            # Remove previous fields
            instance.fields.all().delete()

            created_fields = CollectionField.objects.bulk_create(
                [
                    CollectionField(**field, order=index)
                    for index, field in enumerate(values["fields"])
                ]
            )
            instance.fields.add(*created_fields)

    def before_delete(self, instance):
        instance.fields.all().delete()

    def serialize_property(self, element: Element, prop_name: str):
        """
        You can customize the behavior of the serialization of a property with this
        hook.
        """

        if prop_name == "fields":
            return [
                collection_field_type_registry.get(f.type).export_serialized(f)
                for f in element.fields.all()
            ]

        return super().serialize_property(element, prop_name)

    def deserialize_property(
        self,
        prop_name: str,
        value: Any,
        id_mapping: Dict[str, Any],
        **kwargs,
    ) -> Any:
        if prop_name == "data_source_id" and value:
            return id_mapping["builder_data_sources"][value]

        if prop_name == "fields":
            return [
                # We need to add the data_source_id for the current row
                # provider.
                collection_field_type_registry.get(f["type"]).import_serialized(
                    f, id_mapping, data_source_id=kwargs["data_source_id"]
                )
                for f in value
            ]

        return super().deserialize_property(prop_name, value, id_mapping)

    def create_instance_from_serialized(self, serialized_values: Dict[str, Any]):
        """Deals with the fields"""

        fields = serialized_values.pop("fields", [])

        instance = super().create_instance_from_serialized(serialized_values)

        # Add the field order
        for i, f in enumerate(fields):
            f.order = i

        # Create fields
        created_fields = CollectionField.objects.bulk_create(fields)

        instance.fields.add(*created_fields)

        return instance

    def import_serialized(
        self,
        parent: Any,
        serialized_values: Dict[str, Any],
        id_mapping: Dict[str, Any],
        **kwargs,
    ):
        """
        Here we add the data_source_id to the import process to be able to resolve
        current_record formulas migration.
        """

        actual_data_source_id = None
        if (
            serialized_values.get("data_source_id", None)
            and "builder_data_sources" in id_mapping
        ):
            actual_data_source_id = id_mapping["builder_data_sources"][
                serialized_values["data_source_id"]
            ]

        return super().import_serialized(
            parent,
            serialized_values,
            id_mapping,
            data_source_id=actual_data_source_id,
            **kwargs,
        )


class FormElementTypeMixin:
    # Form element types are imported second, after containers.
    import_element_priority = 1

    def is_valid(self, element: Type[FormElement], value: Any) -> bool:
        """
        Given an element and form data value, returns whether it's valid.
        Used by `FormDataProviderType` to determine if form data is valid.

        :param element: The element we're trying to use form data in.
        :param value: The form data value, which may be invalid.
        :return: Whether the value is valid or not for this element.
        """

        return not (element.required and not value)
