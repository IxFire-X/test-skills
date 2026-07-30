<source_code_and_diff>
  <file path="src/backend/InvenTree/part/api.py">
"""Provides a JSON API for the Part app."""

from django.db.models import Count, F, Q
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

import django_filters.rest_framework.filters as rest_filters
from django_filters.rest_framework import DjangoFilterBackend
from django_filters.rest_framework.filterset import FilterSet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.response import Response

import common.filters
import common.serializers
import part.tasks as part_tasks
from data_exporter.mixins import DataExportViewMixin
from InvenTree.api import (
    BulkDeleteMixin,
    BulkUpdateMixin,
    ListCreateDestroyAPIView,
    ParameterListMixin,
    TreeMixin,
    meta_path,
)
from InvenTree.fields import InvenTreeOutputOption, OutputConfiguration
from InvenTree.filters import (
    ORDER_FILTER,
    SEARCH_ORDER_FILTER,
    InvenTreeDateFilter,
    InvenTreeSearchFilter,
    NumberOrNullFilter,
    NumericInFilter,
)
from InvenTree.helpers import str2bool
from InvenTree.mixins import (
    CreateAPI,
    CustomRetrieveUpdateDestroyAPI,
    ListAPI,
    ListCreateAPI,
    OutputOptionsMixin,
    RetrieveAPI,
    RetrieveUpdateAPI,
    RetrieveUpdateDestroyAPI,
    SerializerContextMixin,
    UpdateAPI,
)
from InvenTree.tasks import offload_task
from stock.models import StockLocation

from . import serializers as part_serializers
from .models import (
    BomItem,
    BomItemSubstitute,
    Part,
    PartCategory,
    PartCategoryParameterTemplate,
    PartInternalPriceBreak,
    PartRelated,
    PartSellPriceBreak,
    PartStocktake,
    PartTestTemplate,
)


class CategoryMixin:
    """Mixin class for PartCategory endpoints."""

    serializer_class = part_serializers.CategorySerializer
    queryset = PartCategory.objects.all()

    def get_queryset(self, *args, **kwargs):
        """Return an annotated queryset for the CategoryDetail endpoint."""
        queryset = super().get_queryset(*args, **kwargs)
        queryset = part_serializers.CategorySerializer.annotate_queryset(queryset)
        return queryset


class CategoryFilter(FilterSet):
    """Custom filterset class for the PartCategoryList endpoint."""

    class Meta:
        """Metaclass options for this filterset."""

        model = PartCategory
        fields = ['name', 'structural']

    starred = rest_filters.BooleanFilter(
        label=_('Starred'),
        method='filter_starred',
        help_text=_('Filter by starred categories'),
    )

    def filter_starred(self, queryset, name, value):
        """Filter by whether the PartCategory is starred by the current user."""
        user = self.request.user

        starred_categories = [
            star.category.pk for star in user.starred_categories.all()
        ]

        if str2bool(value):
            return queryset.filter(pk__in=starred_categories)

        return queryset.exclude(pk__in=starred_categories)

    depth = rest_filters.NumberFilter(
        label=_('Depth'), method='filter_depth', help_text=_('Filter by category depth')
    )

    def filter_depth(self, queryset, name, value):
        """Filter by the "depth" of the PartCategory.

        - This filter is used to limit the depth of the category tree
        - If the "parent" filter is also provided, the depth is calculated from the parent category
        """
        parent = self.data.get('parent', None)

        # Only filter if the parent filter is *not* provided
        if not parent:
            queryset = queryset.filter(level__lte=value)

        return queryset

    top_level = rest_filters.BooleanFilter(
        label=_('Top Level'),
        method='filter_top_level',
        help_text=_('Filter by top-level categories'),
    )

    def filter_top_level(self, queryset, name, value):
        """Filter by top-level categories."""
        cascade = str2bool(self.data.get('cascade', False))

        if value and not cascade:
            return queryset.filter(parent=None)

        return queryset

    cascade = rest_filters.BooleanFilter(
        label=_('Cascade'),
        method='filter_cascade',
        help_text=_('Include sub-categories in filtered results'),
    )

    def filter_cascade(self, queryset, name, value):
        """Filter by whether to include sub-categories in the filtered results.

        Note: If the "parent" filter is provided, we offload the logic to that method.
        """
        parent = self.data.get('parent', None)
        top_level = str2bool(self.data.get('top_level', None))

        # If the parent is *not* provided, update the results based on the "cascade" value
        if (not parent or top_level) and not value:
            # If "cascade" is False, only return top-level categories
            queryset = queryset.filter(parent=None)

        return queryset

    parent = rest_filters.ModelChoiceFilter(
        queryset=PartCategory.objects.all(),
        label=_('Parent'),
        method='filter_parent',
        help_text=_('Filter by parent category'),
    )

    def filter_parent(self, queryset, name, value):
        """Filter by parent category.

        Note that the filtering behaviour here varies,
        depending on whether the 'cascade' value is set.

        So, we have to check the "cascade" value here.
        """
        parent = value
        depth = self.data.get('depth', None)
        cascade = str2bool(self.data.get('cascade', False))

        if cascade:
            # Return recursive subcategories
            queryset = queryset.filter(
                parent__in=parent.get_descendants(include_self=True)
            )
        else:
            # Return only direct children
            queryset = queryset.filter(parent=parent)

        if depth is not None:
            # Filter by depth from parent
            depth = int(depth)
            queryset = queryset.filter(level__lte=parent.level + depth)

        return queryset

    exclude_tree = rest_filters.ModelChoiceFilter(
        queryset=PartCategory.objects.all(),
        label=_('Exclude Tree'),
        method='filter_exclude_tree',
        help_text=_('Exclude sub-categories under the specified category'),
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_exclude_tree(self, queryset, name, value):
        """Exclude all sub-categories under the specified category."""
        # Exclude the specified category
        queryset = queryset.exclude(pk=value.pk)

        # Exclude any sub-categories also
        queryset = queryset.exclude(parent__in=value.get_descendants(include_self=True))

        return queryset


class CategoryOutputOption(OutputConfiguration):
    """Output option for PartCategory endpoints."""

    OPTIONS = [InvenTreeOutputOption(flag='path_detail')]


class CategoryList(
    CategoryMixin,
    BulkUpdateMixin,
    DataExportViewMixin,
    OutputOptionsMixin,
    ListCreateAPI,
):
    """API endpoint for accessing a list of PartCategory objects.

    - GET: Return a list of PartCategory objects
    - POST: Create a new PartCategory object
    """

    filterset_class = CategoryFilter

    filter_backends = SEARCH_ORDER_FILTER

    output_options = CategoryOutputOption

    ordering_fields = ['name', 'pathstring', 'level', 'tree_id', 'lft', 'part_count']

    # Use hierarchical ordering by default
    ordering = ['tree_id', 'lft', 'name']

    search_fields = ['name', 'description', 'pathstring']


class CategoryDetail(CategoryMixin, OutputOptionsMixin, CustomRetrieveUpdateDestroyAPI):
    """API endpoint for detail view of a single PartCategory object."""

    output_options = CategoryOutputOption

    def update(self, request, *args, **kwargs):
        """Perform 'update' function and mark this part as 'starred' (or not)."""
        # Clean up input data
        data = self.clean_data(request.data)
        response = super().update(request, *args, **kwargs)

        if 'starred' in data:
            starred = str2bool(data.get('starred', False))

            self.get_object().set_starred(request.user, starred, include_parents=False)

        return response

    def destroy(self, request, *args, **kwargs):
        """Delete a Part category instance via the API."""
        serializer = part_serializers.CategoryDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delete_parts = str2bool(serializer.validated_data.get('delete_parts', False))
        delete_child_categories = str2bool(
            serializer.validated_data.get('delete_child_categories', False)
        )

        return super().destroy(
            request,
            *args,
            **{
                **kwargs,
                'delete_parts': delete_parts,
                'delete_child_categories': delete_child_categories,
            },
        )


class CategoryTreeFilter(FilterSet):
    """Custom filterset class for the CategoryTree endpoint."""

    class Meta:
        """Metaclass options for this filterset."""

        model = PartCategory
        fields = ['parent', 'tree_id', 'level']

    max_level = rest_filters.NumberFilter(
        label=_('Max Level'),
        method='filter_max_level',
        help_text=_('Limit the depth of the category tree'),
    )

    def filter_max_level(self, queryset, name, value):
        """Filter by the maximum depth of the category tree."""
        return queryset.filter(level__lte=value)


class CategoryTree(TreeMixin, ListAPI):
    """API endpoint for accessing a list of PartCategory objects ready for rendering a tree."""

    model_class = PartCategory
    queryset = PartCategory.objects.all()
    serializer_class = part_serializers.CategoryTreeSerializer
    filterset_class = CategoryTreeFilter

    def get_queryset(self, *args, **kwargs):
        """Return an annotated queryset for the CategoryTree endpoint."""
        queryset = super().get_queryset(*args, **kwargs)
        queryset = part_serializers.CategoryTreeSerializer.annotate_queryset(queryset)
        return queryset


class CategoryParameterList(DataExportViewMixin, OutputOptionsMixin, ListCreateAPI):
    """API endpoint for accessing a list of PartCategoryParameterTemplate objects.

    - GET: Return a list of PartCategoryParameterTemplate objects
    """

    queryset = PartCategoryParameterTemplate.objects.all()
    serializer_class = part_serializers.CategoryParameterTemplateSerializer

    def get_queryset(self):
        """Custom filtering.

        Rules:
        - Allow filtering by "null" parent to retrieve all categories parameter templates
        - Allow filtering by category
        - Allow traversing all parent categories
        """
        queryset = super().get_queryset()

        params = self.request.query_params

        category = params.get('category', None)

        if category is not None:
            try:
                category = PartCategory.objects.get(pk=category)

                fetch_parent = str2bool(params.get('fetch_parent', True))

                if fetch_parent:
                    parents = category.get_ancestors(include_self=True)
                    queryset = queryset.filter(category__in=[cat.pk for cat in parents])
                else:
                    queryset = queryset.filter(category=category)

            except (ValueError, PartCategory.DoesNotExist):
                pass

        return queryset


class CategoryParameterDetail(RetrieveUpdateDestroyAPI):
    """Detail endpoint for the PartCategoryParameterTemplate model."""

    queryset = PartCategoryParameterTemplate.objects.all()
    serializer_class = part_serializers.CategoryParameterTemplateSerializer


class PartSalePriceDetail(RetrieveUpdateDestroyAPI):
    """Detail endpoint for PartSellPriceBreak model."""

    queryset = PartSellPriceBreak.objects.all()
    serializer_class = part_serializers.PartSalePriceSerializer


class PartSalePriceList(DataExportViewMixin, ListCreateAPI):
    """API endpoint for list view of PartSalePriceBreak model."""

    queryset = PartSellPriceBreak.objects.all()
    serializer_class = part_serializers.PartSalePriceSerializer

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['part']
    ordering_fields = ['quantity', 'price']
    ordering = 'quantity'


class PartInternalPriceDetail(RetrieveUpdateDestroyAPI):
    """Detail endpoint for PartInternalPriceBreak model."""

    queryset = PartInternalPriceBreak.objects.all()
    serializer_class = part_serializers.PartInternalPriceSerializer


class PartInternalPriceList(DataExportViewMixin, ListCreateAPI):
    """API endpoint for list view of PartInternalPriceBreak model."""

    queryset = PartInternalPriceBreak.objects.all()
    serializer_class = part_serializers.PartInternalPriceSerializer
    permission_required = 'roles.sales_order.show'

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['part']
    ordering_fields = ['quantity', 'price']
    ordering = 'quantity'


class PartTestTemplateFilter(FilterSet):
    """Custom filterset class for the PartTestTemplateList endpoint."""

    class Meta:
        """Metaclass options for this filterset."""

        model = PartTestTemplate
        fields = ['enabled', 'key', 'required', 'requires_attachment', 'requires_value']

    part = rest_filters.ModelChoiceFilter(
        queryset=Part.objects.filter(testable=True),
        label='Part',
        field_name='part',
        method='filter_part',
    )

    def filter_part(self, queryset, name, part):
        """Filter by the 'part' field.

        Note: If the 'include_inherited' query parameter is set,
        we also include any parts "above" the specified part.
        """
        include_inherited = str2bool(
            self.request.query_params.get('include_inherited', True)
        )

        if include_inherited:
            return queryset.filter(part__in=part.get_ancestors(include_self=True))
        else:
            return queryset.filter(part=part)

    has_results = rest_filters.BooleanFilter(
        label=_('Has Results'), method='filter_has_results'
    )

    def filter_has_results(self, queryset, name, value):
        """Filter by whether the PartTestTemplate has any associated test results."""
        if str2bool(value):
            return queryset.exclude(results=0)
        return queryset.filter(results=0)


class PartTestTemplateMixin:
    """Mixin class for the PartTestTemplate API endpoints."""

    queryset = PartTestTemplate.objects.all()
    serializer_class = part_serializers.PartTestTemplateSerializer

    def get_queryset(self, *args, **kwargs):
        """Return an annotated queryset for the PartTestTemplateDetail endpoints."""
        queryset = super().get_queryset(*args, **kwargs)
        queryset = part_serializers.PartTestTemplateSerializer.annotate_queryset(
            queryset
        )
        return queryset


class PartTestTemplateDetail(PartTestTemplateMixin, RetrieveUpdateDestroyAPI):
    """Detail endpoint for PartTestTemplate model."""


class PartTestTemplateList(PartTestTemplateMixin, DataExportViewMixin, ListCreateAPI):
    """API endpoint for listing (and creating) a PartTestTemplate."""

    filterset_class = PartTestTemplateFilter

    filter_backends = SEARCH_ORDER_FILTER

    search_fields = ['test_name', 'description']

    ordering_fields = [
        'enabled',
        'required',
        'requires_value',
        'requires_attachment',
        'results',
        'test_name',
    ]

    ordering = 'test_name'


class PartThumbs(ListAPI):
    """API endpoint for retrieving information on available Part thumbnails."""

    queryset = Part.objects.all()
    serializer_class = part_serializers.PartThumbSerializer

    def get_queryset(self):
        """Return a queryset which excludes any parts without images."""
        queryset = super().get_queryset()

        # Get all Parts which have an associated image
        queryset = queryset.exclude(image='')

        return queryset

    def list(self, request, *args, **kwargs):
        """Serialize the available Part images.

        - Images may be used for multiple parts!
        """
        queryset = self.filter_queryset(self.get_queryset())

        # Return the most popular parts first
        data = (
            queryset.values('image').annotate(count=Count('image')).order_by('-count')
        )

        page = self.paginate_queryset(data)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
        else:
            serializer = self.get_serializer(data, many=True)

        data = serializer.data

        if page is not None:
            return self.get_paginated_response(data)
        else:
            return Response(data)

    filter_backends = [InvenTreeSearchFilter]

    search_fields = [
        'name',
        'description',
        'IPN',
        'revision',
        'keywords',
        'category__name',
    ]


class PartThumbsUpdate(RetrieveUpdateAPI):
    """API endpoint for updating Part thumbnails."""

    queryset = Part.objects.all()
    serializer_class = part_serializers.PartThumbSerializerUpdate

    filter_backends = [DjangoFilterBackend]


class PartRequirements(RetrieveAPI):
    """API endpoint detailing 'requirements' information for a particular part.

    This endpoint returns information on upcoming requirements for:

    - Sales Orders
    - Build Orders
    - Total requirements
    - How many of this part can be assembled with available stock

    As this data is somewhat complex to calculate, is it not included in the default API
    """

    queryset = Part.objects.all()
    serializer_class = part_serializers.PartRequirementsSerializer


class PartPricingDetail(RetrieveUpdateAPI):
    """API endpoint for viewing part pricing data."""

    serializer_class = part_serializers.PartPricingSerializer
    queryset = Part.objects.all().select_related('pricing_data')

    def get_object(self):
        """Return the PartPricing object associated with the linked Part."""
        part = super().get_object()
        return part.pricing

    def _get_serializer(self, *args, **kwargs):
        """Return a part pricing serializer object."""
        part = self.get_object()
        kwargs['instance'] = part.pricing

        return self.serializer_class(**kwargs)


class PartSerialNumberDetail(RetrieveAPI):
    """API endpoint for returning extra serial number information about a particular part."""

    queryset = Part.objects.all()
    serializer_class = part_serializers.PartSerialNumberSerializer


class PartCopyBOM(CreateAPI):
    """API endpoint for duplicating a BOM."""

    queryset = Part.objects.all()
    serializer_class = part_serializers.PartCopyBOMSerializer

    def get_serializer_context(self):
        """Add custom information to the serializer context for this endpoint."""
        ctx = super().get_serializer_context()

        try:
            ctx['part'] = Part.objects.get(pk=self.kwargs.get('pk', None))
        except Exception:
            pass

        return ctx


class PartValidateBOM(RetrieveUpdateAPI):
    """API endpoint for 'validating' the BOM for a given Part."""

    queryset = Part.objects.all()
    serializer_class = part_serializers.PartBomValidateSerializer
    role_required = 'bom'

    @extend_schema(
        responses={
            200: common.serializers.TaskDetailSerializer,
            404: common.serializers.TaskDetailSerializer,
        }
    )
    def update(self, request, *args, **kwargs):
        """Validate the referenced BomItem instance.

        As this task is offloaded to the background worker,
        we return information about the background task which is performing the validation.
        """
        part = self.get_object()

        partial = kwargs.pop('partial', False)

        # Clean up input data before using it
        data = self.clean_data(request.data)

        serializer = self.get_serializer(part, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)

        valid = str2bool(serializer.validated_data.get('valid', False))

        # BOM validation may take some time, so we offload it to a background task
        task_id = offload_task(
            part_tasks.validate_bom,
            part.pk,
            valid,
            user_id=request.user.pk if request and request.user else None,
            group='part',
        )

        response = common.serializers.TaskDetailSerializer.from_task(task_id).data
        return Response(response, status=response['http_status'])


class PartFilter(FilterSet):
    """Custom filters for the PartList endpoint.

    Uses the django_filters extension framework
    """

    class Meta:
        """Metaclass options for this filter set."""

        model = Part
        fields = ['revision_of']

    is_variant = rest_filters.BooleanFilter(
        label=_('Is Variant'), method='filter_is_variant'
    )

    def filter_is_variant(self, queryset, name, value):
        """Filter by whether the Part is a variant or not."""
        return queryset.filter(variant_of__isnull=not str2bool(value))

    is_revision = rest_filters.BooleanFilter(
        label=_('Is Revision'), method='filter_is_revision'
    )

    def filter_is_revision(self, queryset, name, value):
        """Filter by whether the Part is a revision or not."""
        if str2bool(value):
            return queryset.exclude(revision_of=None)
        return queryset.filter(revision_of=None)

    has_revisions = rest_filters.BooleanFilter(
        label=_('Has Revisions'), method='filter_has_revisions'
    )

    def filter_has_revisions(self, queryset, name, value):
        """Filter by whether the Part has any revisions or not."""
        if str2bool(value):
            return queryset.exclude(revision_count=0)
        return queryset.filter(revision_count=0)

    has_units = rest_filters.BooleanFilter(label='Has units', method='filter_has_units')

    def filter_has_units(self, queryset, name, value):
        """Filter by whether the Part has units or not."""
        if str2bool(value):
            return queryset.exclude(Q(units=None) | Q(units=''))

        return queryset.filter(Q(units=None) | Q(units='')).distinct()

    # Filter by parts which have (or not) an IPN value
    has_ipn = rest_filters.BooleanFilter(label='Has IPN', method='filter_has_ipn')

    def filter_has_ipn(self, queryset, name, value):
        """Filter by whether the Part has an IPN (internal part number) or not."""
        if str2bool(value):
            return queryset.exclude(IPN='').exclude(IPN=None)
        return queryset.filter(Q(IPN='') | Q(IPN=None)).distinct()

    # Regex filter for name
    name_regex = rest_filters.CharFilter(
        label='Filter by name (regex)', field_name='name', lookup_expr='iregex'
    )

    # Exact match for IPN
    IPN = rest_filters.CharFilter(
        label='Filter by exact IPN (internal part number)',
        field_name='IPN',
        lookup_expr='iexact',
    )

    # Regex match for IPN
    IPN_regex = rest_filters.CharFilter(
        label='Filter by regex on IPN (internal part number)',
        field_name='IPN',
        lookup_expr='iregex',
    )

    # low_stock filter
    low_stock = rest_filters.BooleanFilter(label='Low stock', method='filter_low_stock')

    def filter_low_stock(self, queryset, name, value):
        """Filter by "low stock" status."""
        if str2bool(value):
            # Ignore any parts which do not have a specified 'minimum_stock' level
            # Filter items which have an 'in_stock' level lower than 'minimum_stock'
            return queryset.exclude(minimum_stock=0).filter(
                Q(total_in_stock__lt=F('minimum_stock'))
            )
        # Filter items which have an 'in_stock' level higher than 'minimum_stock'
        return queryset.filter(Q(total_in_stock__gte=F('minimum_stock')))

    high_stock = rest_filters.BooleanFilter(
        label='High stock', method='filter_high_stock'
    )

    def filter_high_stock(self, queryset, name, value):
        """Filter by "high stock" status."""
        if str2bool(value):
            # Ignore any parts which do not have a specified 'maximum_stock' level
            # Filter items which have an 'in_stock' level higher than 'maximum_stock'
            return queryset.exclude(maximum_stock=0).filter(
                Q(total_in_stock__gt=F('maximum_stock'))
            )
        # Filter items which have an 'in_stock' level lower than 'maximum_stock'
        return queryset.filter(
            Q(total_in_stock__lte=F('maximum_stock')) | Q(maximum_stock=0)
        ).distinct()

    # has_stock filter
    has_stock = rest_filters.BooleanFilter(label='Has stock', method='filter_has_stock')

    def filter_has_stock(self, queryset, name, value):
        """Filter by whether the Part has any stock."""
        if str2bool(value):
            return queryset.filter(Q(in_stock__gt=0))
        return queryset.filter(Q(in_stock__lte=0))

    # unallocated_stock filter
    unallocated_stock = rest_filters.BooleanFilter(
        label='Unallocated stock', method='filter_unallocated_stock'
    )

    def filter_unallocated_stock(self, queryset, name, value):
        """Filter by whether the Part has unallocated stock."""
        if str2bool(value):
            return queryset.filter(Q(unallocated_stock__gt=0))
        return queryset.filter(Q(unallocated_stock__lte=0))

    # on_order filter
    on_order = rest_filters.BooleanFilter(label='On order', method='filter_on_order')

    def filter_on_order(self, queryset, name, value):
        """Filter by whether the Part has any stock on order."""
        if str2bool(value):
            return queryset.filter(Q(ordering__gt=0))
        return queryset.filter(Q(ordering__lte=0))

    convert_from = rest_filters.ModelChoiceFilter(
        label='Can convert from',
        queryset=Part.objects.all(),
        method='filter_convert_from',
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_convert_from(self, queryset, name, part):
        """Limit the queryset to valid conversion options for the specified part."""
        conversion_options = part.get_conversion_options()

        queryset = queryset.filter(pk__in=conversion_options)

        return queryset

    exclude_tree = rest_filters.ModelChoiceFilter(
        label='Exclude Part tree',
        queryset=Part.objects.all(),
        method='filter_exclude_tree',
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_exclude_tree(self, queryset, name, part):
        """Exclude all parts and variants 'down' from the specified part from the queryset."""
        children = part.get_descendants(include_self=True)

        return queryset.exclude(id__in=children)

    ancestor = rest_filters.ModelChoiceFilter(
        label='Ancestor', queryset=Part.objects.all(), method='filter_ancestor'
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_ancestor(self, queryset, name, part):
        """Limit queryset to descendants of the specified ancestor part."""
        descendants = part.get_descendants(include_self=False)
        return queryset.filter(id__in=descendants)

    variant_of = rest_filters.ModelChoiceFilter(
        label='Variant Of', queryset=Part.objects.all(), method='filter_variant_of'
    )

    def filter_variant_of(self, queryset, name, part):
        """Limit queryset to direct children (variants) of the specified part."""
        return queryset.filter(id__in=part.get_children())

    in_bom_for = rest_filters.ModelChoiceFilter(
        label='In BOM Of', queryset=Part.objects.all(), method='filter_in_bom'
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_in_bom(self, queryset, name, part):
        """Limit queryset to parts in the BOM for the specified part."""
        bom_parts = part.get_parts_in_bom()
        return queryset.filter(id__in=[p.pk for p in bom_parts])

    has_pricing = rest_filters.BooleanFilter(
        label='Has Pricing', method='filter_has_pricing'
    )

    def filter_has_pricing(self, queryset, name, value):
        """Filter the queryset based on whether pricing information is available for the sub_part."""
        q_a = Q(pricing_data=None)
        q_b = Q(pricing_data__overall_min=None, pricing_data__overall_max=None)

        if str2bool(value):
            return queryset.exclude(q_a | q_b)

        return queryset.filter(q_a | q_b).distinct()

    stock_to_build = rest_filters.BooleanFilter(
        label='Required for Build Order', method='filter_stock_to_build'
    )

    def filter_stock_to_build(self, queryset, name, value):
        """Filter the queryset based on whether part stock is required for a pending BuildOrder."""
        if str2bool(value):
            # Return parts which are required for a build order, but have not yet been allocated
            return queryset.filter(
                required_for_build_orders__gt=F('allocated_to_build_orders')
            )
        # Return parts which are not required for a build order, or have already been allocated
        return queryset.filter(
            required_for_build_orders__lte=F('allocated_to_build_orders')
        )

    depleted_stock = rest_filters.BooleanFilter(
        label='Depleted Stock', method='filter_depleted_stock'
    )

    def filter_depleted_stock(self, queryset, name, value):
        """Filter the queryset based on whether the part is fully depleted of stock."""
        if str2bool(value):
            return queryset.filter(Q(in_stock=0) &amp; ~Q(stock_item_count=0))
        return queryset.exclude(Q(in_stock=0) &amp; ~Q(stock_item_count=0))

    default_location = rest_filters.ModelChoiceFilter(
        label='Default Location', queryset=StockLocation.objects.all()
    )

    bom_valid = rest_filters.BooleanFilter(
        label=_('BOM Valid'), field_name='bom_validated'
    )

    starred = rest_filters.BooleanFilter(label='Starred', method='filter_starred')

    def filter_starred(self, queryset, name, value):
        """Filter by whether the Part is 'starred' by the current user."""
        if self.request.user.is_anonymous:
            return queryset

        starred_parts = [
            star.part.pk
            for star in self.request.user.starred_parts.all().prefetch_related('part')
        ]

        if value:
            return queryset.filter(pk__in=starred_parts)
        else:
            return queryset.exclude(pk__in=starred_parts)

    is_template = rest_filters.BooleanFilter()

    assembly = rest_filters.BooleanFilter()

    component = rest_filters.BooleanFilter()

    trackable = rest_filters.BooleanFilter()

    testable = rest_filters.BooleanFilter()

    purchaseable = rest_filters.BooleanFilter()

    salable = rest_filters.BooleanFilter()

    active = rest_filters.BooleanFilter()

    locked = rest_filters.BooleanFilter()

    virtual = rest_filters.BooleanFilter()

    consumable = rest_filters.BooleanFilter()

    tags = common.filters.TagsFilter()

    # Created date filters
    created_before = InvenTreeDateFilter(
        label='Updated before', field_name='creation_date', lookup_expr='lt'
    )
    created_after = InvenTreeDateFilter(
        label='Updated after', field_name='creation_date', lookup_expr='gt'
    )

    exclude_id = NumericInFilter(
        field_name='id',
        lookup_expr='in',
        exclude=True,
        help_text='Exclude parts with these IDs (comma-separated)',
    )

    related = rest_filters.NumberFilter(
        method='filter_related_parts', help_text='Show parts related to this part ID'
    )

    exclude_related = rest_filters.NumberFilter(
        method='filter_exclude_related_parts',
        help_text='Exclude parts related to this part ID',
    )

    def filter_related_parts(self, queryset, name, value):
        """Filter parts related to the specified part ID."""
        if not value:
            return queryset

        try:
            related_part = Part.objects.get(pk=value)
            part_ids = self._get_related_part_ids(related_part)
            return queryset.filter(pk__in=list(part_ids))
        except (ValueError, Part.DoesNotExist):
            return queryset.none()

    def filter_exclude_related_parts(self, queryset, name, value):
        """Exclude parts related to the specified part ID."""
        if not value:
            return queryset

        try:
            related_part = Part.objects.get(pk=value)
            part_ids = self._get_related_part_ids(related_part)
            return queryset.exclude(pk__in=list(part_ids))
        except (ValueError, Part.DoesNotExist):
            return queryset

    def _get_related_part_ids(self, related_part):
        """Return a set of part IDs which are related to the specified part."""
        part_ids = set()
        pk = related_part.pk

        relation_filter = Q(part_1=related_part) | Q(part_2=related_part)

        for relation in PartRelated.objects.filter(relation_filter).distinct():
            if relation.part_1.pk != pk:
                part_ids.add(relation.part_1.pk)
            if relation.part_2.pk != pk:
                part_ids.add(relation.part_2.pk)

        return part_ids

    cascade = rest_filters.BooleanFilter(
        method='filter_cascade',
        label=_('Cascade Categories'),
        help_text=_('If true, include items in child categories of the given category'),
    )

    category = NumberOrNullFilter(
        method='filter_category',
        label=_('Category'),
        help_text=_("Filter by numeric category ID or the literal 'null'"),
    )

    def filter_cascade(self, queryset, name, value):
        """Dummy filter method for 'cascade'.

        - Ensures 'cascade' appears in API documentation
        - Does NOT actually filter the queryset directly
        """
        return queryset

    def filter_category(self, queryset, name, value):
        """Filter for category that also applies cascade logic."""
        cascade = str2bool(self.data.get('cascade', True))

        if value == 'null':
            if not cascade:
                return queryset.filter(category=None)
            return queryset

        if not cascade:
            return queryset.filter(category=value)

        try:
            category = PartCategory.objects.get(pk=value)
        except PartCategory.DoesNotExist:
            return queryset

        children = category.getUniqueChildren()
        return queryset.filter(category__in=children)


class PartMixin(SerializerContextMixin):
    """Mixin class for Part API endpoints."""

    serializer_class = part_serializers.PartSerializer
    queryset = (
        Part.objects.all().select_related('pricing_data').prefetch_related('category')
    )

    starred_parts = None
    is_create = False

    def get_queryset(self, *args, **kwargs):
        """Return an annotated queryset object for the PartDetail endpoint."""
        queryset = super().get_queryset(*args, **kwargs)

        queryset = part_serializers.PartSerializer.annotate_queryset(queryset)

        return queryset

    def get_serializer(self, *args, **kwargs):
        """Return a serializer instance for this endpoint."""
        # Indicate that we can create a new Part via this endpoint
        kwargs['create'] = self.is_create

        return super().get_serializer(*args, **kwargs)


class PartOutputOptions(OutputConfiguration):
    """Output options for Part endpoints."""

    OPTIONS = [
        InvenTreeOutputOption(
            'parameters', description='Include part parameters in response'
        ),
        InvenTreeOutputOption('category_detail'),
        InvenTreeOutputOption('location_detail'),
        InvenTreeOutputOption('path_detail'),
        InvenTreeOutputOption('price_breaks'),
        InvenTreeOutputOption('tags'),
    ]


class PartList(
    PartMixin,
    BulkUpdateMixin,
    ParameterListMixin,
    DataExportViewMixin,
    OutputOptionsMixin,
    ListCreateAPI,
):
    """API endpoint for accessing a list of Part objects, or creating a new Part instance."""

    output_options = PartOutputOptions
    filterset_class = PartFilter
    is_create = True

    filter_backends = SEARCH_ORDER_FILTER

    ordering_fields = [
        'id',
        'name',
        'creation_date',
        'IPN',
        'ordering',
        'in_stock',
        'total_in_stock',
        'unallocated_stock',
        'category',
        'default_location',
        'units',
        'pricing_min',
        'pricing_max',
        'pricing_updated',
        'revision',
        'revision_count',
    ]

    ordering_field_aliases = {
        'pricing_min': 'pricing_data__overall_min',
        'pricing_max': 'pricing_data__overall_max',
        'pricing_updated': 'pricing_data__updated',
    }

    # Default ordering
    ordering = 'name'

    search_fields = [
        'name',
        'description',
        'IPN',
        'revision',
        'keywords',
        'category__name',
        'manufacturer_parts__MPN',
        'supplier_parts__SKU',
        'tags__name',
        'tags__slug',
    ]


class PartDetail(PartMixin, OutputOptionsMixin, RetrieveUpdateDestroyAPI):
    """API endpoint for detail view of a single Part object."""

    output_options = PartOutputOptions

    def update(self, request, *args, **kwargs):
        """Custom update functionality for Part instance.

        - If the 'starred' field is provided, update the 'starred' status against current user
        """
        # Clean input data
        data = self.clean_data(request.data)
        response = super().update(request, *args, **kwargs)

        if 'starred' in data:
            starred = str2bool(data.get('starred', False))

            self.get_object().set_starred(
                request.user, starred, include_variants=False, include_categories=False
            )

        return response


class PartRelatedFilter(FilterSet):
    """FilterSet for PartRelated objects."""

    class Meta:
        """Metaclass options."""

        model = PartRelated
        fields = ['part_1', 'part_2']

    part = rest_filters.ModelChoiceFilter(
        queryset=Part.objects.all(), method='filter_part', label=_('Part')
    )

    @extend_schema_field(serializers.IntegerField(help_text=_('Part')))
    def filter_part(self, queryset, name, part):
        """Filter queryset to include only PartRelated objects which reference the specified part."""
        return queryset.filter(Q(part_1=part) | Q(part_2=part)).distinct()


class PartRelatedMixin:
    """Mixin class for PartRelated API endpoints."""

    queryset = PartRelated.objects.all()
    serializer_class = part_serializers.PartRelationSerializer

    def get_queryset(self, *args, **kwargs):
        """Return an annotated queryset for the PartRelatedDetail endpoint."""
        queryset = super().get_queryset(*args, **kwargs)

        queryset = queryset.prefetch_related('part_1', 'part_2')

        return queryset


class PartRelatedList(PartRelatedMixin, ListCreateAPI):
    """API endpoint for accessing a list of PartRelated objects."""

    filterset_class = PartRelatedFilter
    filter_backends = SEARCH_ORDER_FILTER

    search_fields = ['part_1__name', 'part_2__name']


class PartRelatedDetail(PartRelatedMixin, RetrieveUpdateDestroyAPI):
    """API endpoint for accessing detail view of a PartRelated object."""


class PartStocktakeFilter(FilterSet):
    """Custom filter for the PartStocktakeList endpoint."""

    class Meta:
        """Metaclass options."""

        model = PartStocktake
        fields = ['part']


class PartStocktakeMixin:
    """Mixin class for PartStocktake API endpoints."""

    queryset = PartStocktake.objects.all().prefetch_related('part')
    serializer_class = part_serializers.PartStocktakeSerializer


class PartStocktakeList(
    PartStocktakeMixin, DataExportViewMixin, BulkDeleteMixin, ListCreateAPI
):
    """API endpoint for listing part stocktake information."""

    filterset_class = PartStocktakeFilter

    def get_serializer_context(self):
        """Extend serializer context data."""
        context = super().get_serializer_context()
        context['request'] = self.request

        return context

    filter_backends = ORDER_FILTER

    ordering_fields = ['part', 'item_count', 'quantity', 'date', 'user', 'pk']

    # Reverse date ordering by default
    ordering = '-pk'


class PartStocktakeDetail(PartStocktakeMixin, RetrieveUpdateDestroyAPI):
    """Detail API endpoint for a single PartStocktake instance.

    Note: Only staff (admin) users can access this endpoint.
    """


class PartStocktakeGenerate(CreateAPI):
    """API endpoint for generating a PartStocktake instance."""

    queryset = PartStocktake.objects.all()
    serializer_class = part_serializers.PartStocktakeGenerateSerializer

    def post(self, request, *args, **kwargs):
        """Perform stocktake generation on POST request."""
        from common.models import DataOutput
        from part.stocktake import perform_stocktake

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        part = data.get('part', None)
        category = data.get('category', None)
        location = data.get('location', None)

        # Do we want to generate a report?
        if data.get('generate_report', True):
            report_output = DataOutput.objects.create(
                user=request.user, output_type='stocktake'
            )
        else:
            report_output = None

        # Offload the actual stocktake generation to a background task, as it may take some time to complete
        offload_task(
            perform_stocktake,
            part_id=part.pk if part else None,
            category_id=category.pk if category else None,
            location_id=location.pk if location else None,
            generate_entry=data.get('generate_entry', True),
            report_output_id=report_output.pk if report_output else None,
            group='stocktake',
        )

        if report_output:
            report_output.refresh_from_db()

        result = {
            'category': category,
            'location': location,
            'part': part,
            'output': report_output,
        }

        output_serializer = part_serializers.PartStocktakeGenerateSerializer(result)

        return Response(output_serializer.data)


class BomFilter(FilterSet):
    """Custom filters for the BOM list."""

    class Meta:
        """Metaclass options."""

        model = BomItem
        fields = ['optional', 'consumable', 'inherited', 'allow_variants', 'validated']

    # Filters for linked 'part'
    part_active = rest_filters.BooleanFilter(
        label=_('Assembly part is active'), field_name='part__active'
    )

    part_trackable = rest_filters.BooleanFilter(
        label=_('Assembly part is trackable'), field_name='part__trackable'
    )

    part_testable = rest_filters.BooleanFilter(
        label=_('Assembly part is testable'), field_name='part__testable'
    )

    part_locked = rest_filters.BooleanFilter(
        label=_('Assembly part is locked'), field_name='part__locked'
    )

    # Filters for linked 'sub_part'
    sub_part_active = rest_filters.BooleanFilter(
        label=_('Component part is active'), field_name='sub_part__active'
    )

    sub_part_trackable = rest_filters.BooleanFilter(
        label=_('Component part is trackable'), field_name='sub_part__trackable'
    )

    sub_part_testable = rest_filters.BooleanFilter(
        label=_('Component part is testable'), field_name='sub_part__testable'
    )

    sub_part_assembly = rest_filters.BooleanFilter(
        label=_('Component part is an assembly'), field_name='sub_part__assembly'
    )

    sub_part_virtual = rest_filters.BooleanFilter(
        label=_('Component part is virtual'), field_name='sub_part__virtual'
    )

    available_stock = rest_filters.BooleanFilter(
        label=_('Has available stock'), method='filter_available_stock'
    )

    def filter_available_stock(self, queryset, name, value):
        """Filter the queryset based on whether each line item has any available stock."""
        if str2bool(value):
            return queryset.filter(available_stock__gt=0)
        return queryset.filter(available_stock=0)

    on_order = rest_filters.BooleanFilter(label='On order', method='filter_on_order')

    def filter_on_order(self, queryset, name, value):
        """Filter the queryset based on whether each line item has any stock on order."""
        if str2bool(value):
            return queryset.filter(on_order__gt=0)
        return queryset.filter(on_order=0)

    has_pricing = rest_filters.BooleanFilter(
        label='Has Pricing', method='filter_has_pricing'
    )

    def filter_has_pricing(self, queryset, name, value):
        """Filter the queryset based on whether pricing information is available for the sub_part."""
        q_a = Q(sub_part__pricing_data=None)
        q_b = Q(
            sub_part__pricing_data__overall_min=None,
            sub_part__pricing_data__overall_max=None,
        )

        if str2bool(value):
            return queryset.exclude(q_a | q_b)

        return queryset.filter(q_a | q_b).distinct()

    part = rest_filters.ModelChoiceFilter(
        queryset=Part.objects.all(), method='filter_part', label=_('Part')
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_part(self, queryset, name, part):
        """Filter the queryset based on the specified part."""
        return queryset.filter(part.get_bom_item_filter())

    category = rest_filters.ModelChoiceFilter(
        queryset=PartCategory.objects.all(),
        method='filter_category',
        label=_('Category'),
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_category(self, queryset, name, category):
        """Filter the queryset based on the specified PartCategory."""
        cats = category.get_descendants(include_self=True)

        return queryset.filter(sub_part__category__in=cats)

    uses = rest_filters.ModelChoiceFilter(
        queryset=Part.objects.all(), method='filter_uses', label=_('Uses')
    )

    @extend_schema_field(OpenApiTypes.INT)
    def filter_uses(self, queryset, name, part):
        """Filter the queryset based on the specified part."""
        return queryset.filter(part.get_used_in_bom_item_filter())


class BomMixin(SerializerContextMixin):
    """Mixin class for BomItem API endpoints."""

    serializer_class = part_serializers.BomItemSerializer
    queryset = BomItem.objects.all()

    def get_queryset(self, *args, **kwargs):
        """Return the queryset object for this endpoint."""
        queryset = super().get_queryset(*args, **kwargs)

        queryset = self.get_serializer_class().annotate_queryset(queryset)

        return queryset


class BomOutputOptions(OutputConfiguration):
    """Output options for BOM endpoints."""

    OPTIONS = [
        InvenTreeOutputOption('can_build', default=True),
        InvenTreeOutputOption('part_detail'),
        InvenTreeOutputOption('sub_part_detail'),
        InvenTreeOutputOption('substitutes'),
        InvenTreeOutputOption('pricing'),
    ]


class BomList(
    BomMixin,
    BulkUpdateMixin,
    DataExportViewMixin,
    OutputOptionsMixin,
    ListCreateDestroyAPIView,
):
    """API endpoint for accessing a list of BomItem objects.

    - GET: Return list of BomItem objects
    - POST: Create a new BomItem object
    """

    output_options = BomOutputOptions
    filterset_class = BomFilter
    filter_backends = SEARCH_ORDER_FILTER

    search_fields = [
        'reference',
        'part__name',
        'part__description',
        'part__IPN',
        'part__revision',
        'part__keywords',
        'sub_part__name',
        'sub_part__description',
        'sub_part__IPN',
        'sub_part__revision',
        'sub_part__keywords',
        'sub_part__category__name',
    ]

    ordering_fields = [
        'can_build',
        'category',
        'quantity',
        'setup_quantity',
        'attrition',
        'rounding_multiple',
        'sub_part',
        'IPN',
        'available_stock',
        'allow_variants',
        'inherited',
        'optional',
        'consumable',
        'reference',
        'validated',
        'pricing_min',
        'pricing_max',
        'pricing_min_total',
        'pricing_max_total',
        'pricing_updated',
    ]

    ordering_field_aliases = {
        'category': 'sub_part__category__name',
        'sub_part': 'sub_part__name',
        'IPN': 'sub_part__IPN',
        'pricing_min': 'sub_part__pricing_data__overall_min',
        'pricing_max': 'sub_part__pricing_data__overall_max',
        'pricing_updated': 'sub_part__pricing_data__updated',
    }

    def validate_delete(self, queryset, request) -&gt; None:
        """Ensure that there are no 'locked' items."""
        for bom_item in queryset:
            # Note: Calling check_part_lock may raise a ValidationError
            bom_item.check_part_lock(bom_item.part)


class BomDetail(BomMixin, OutputOptionsMixin, RetrieveUpdateDestroyAPI):
    """API endpoint for detail view of a single BomItem object."""

    output_options = BomOutputOptions


class BomItemValidate(UpdateAPI):
    """API endpoint for validating a BomItem."""

    class BomItemValidationSerializer(serializers.Serializer):
        """Simple serializer for passing a single boolean field."""

        valid = serializers.BooleanField(default=False)

    queryset = BomItem.objects.all()
    serializer_class = BomItemValidationSerializer

    def update(self, request, *args, **kwargs):
        """Perform update request."""
        partial = kwargs.pop('partial', False)

        # Clean up input data
        data = self.clean_data(request.data)
        valid = data.get('valid', False)

        instance = self.get_object()

        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)

        if isinstance(instance, BomItem):
            instance.validate_hash(valid)

        return Response(serializer.data)


class BomItemSubstituteList(ListCreateAPI):
    """API endpoint for accessing a list of BomItemSubstitute objects."""

    serializer_class = part_serializers.BomItemSubstituteSerializer
    queryset = BomItemSubstitute.objects.all()

    filter_backends = SEARCH_ORDER_FILTER

    filterset_fields = ['part', 'bom_item']


class BomItemSubstituteDetail(RetrieveUpdateDestroyAPI):
    """API endpoint for detail view of a single BomItemSubstitute object."""

    queryset = BomItemSubstitute.objects.all()
    serializer_class = part_serializers.BomItemSubstituteSerializer


part_api_urls = [
    # Base URL for PartCategory API endpoints
    path(
        'category/',
        include([
            path('tree/', CategoryTree.as_view(), name='api-part-category-tree'),
            path(
                'parameters/',
                include([
                    path(
                        '&lt;int:pk&gt;/',
                        include([
                            meta_path(PartCategoryParameterTemplate),
                            path(
                                '',
                                CategoryParameterDetail.as_view(),
                                name='api-part-category-parameter-detail',
                            ),
                        ]),
                    ),
                    path(
                        '',
                        CategoryParameterList.as_view(),
                        name='api-part-category-parameter-list',
                    ),
                ]),
            ),
            # Category detail endpoints
            path(
                '&lt;int:pk&gt;/',
                include([
                    meta_path(PartCategory),
                    # PartCategory detail endpoint
                    path('', CategoryDetail.as_view(), name='api-part-category-detail'),
                ]),
            ),
            path('', CategoryList.as_view(), name='api-part-category-list'),
        ]),
    ),
    # Base URL for PartTestTemplate API endpoints
    path(
        'test-template/',
        include([
            path(
                '&lt;int:pk&gt;/',
                include([
                    meta_path(PartTestTemplate),
                    path(
                        '',
                        PartTestTemplateDetail.as_view(),
                        name='api-part-test-template-detail',
                    ),
                ]),
            ),
            path(
                '', PartTestTemplateList.as_view(), name='api-part-test-template-list'
            ),
        ]),
    ),
    # Base URL for part sale pricing
    path(
        'sale-price/',
        include([
            path(
                '&lt;int:pk&gt;/',
                PartSalePriceDetail.as_view(),
                name='api-part-sale-price-detail',
            ),
            path('', PartSalePriceList.as_view(), name='api-part-sale-price-list'),
        ]),
    ),
    # Base URL for part internal pricing
    path(
        'internal-price/',
        include([
            path(
                '&lt;int:pk&gt;/',
                PartInternalPriceDetail.as_view(),
                name='api-part-internal-price-detail',
            ),
            path(
                '', PartInternalPriceList.as_view(), name='api-part-internal-price-list'
            ),
        ]),
    ),
    # Base URL for PartRelated API endpoints
    path(
        'related/',
        include([
            path(
                '&lt;int:pk&gt;/',
                include([
                    meta_path(PartRelated),
                    path(
                        '', PartRelatedDetail.as_view(), name='api-part-related-detail'
                    ),
                ]),
            ),
            path('', PartRelatedList.as_view(), name='api-part-related-list'),
        ]),
    ),
    # Part stocktake data
    path(
        'stocktake/',
        include([
            path(
                '&lt;int:pk&gt;/',
                PartStocktakeDetail.as_view(),
                name='api-part-stocktake-detail',
            ),
            path(
                'generate/',
                PartStocktakeGenerate.as_view(),
                name='api-part-stocktake-generate',
            ),
            path('', PartStocktakeList.as_view(), name='api-part-stocktake-list'),
        ]),
    ),
    path(
        'thumbs/',
        include([
            path('', PartThumbs.as_view(), name='api-part-thumbs'),
            path(
                '&lt;int:pk&gt;/', PartThumbsUpdate.as_view(), name='api-part-thumbs-update'
            ),
        ]),
    ),
    path(
        '&lt;int:pk&gt;/',
        include([
            # Endpoint for extra serial number information
            path(
                'serial-numbers/',
                PartSerialNumberDetail.as_view(),
                name='api-part-serial-number-detail',
            ),
            path(
                'requirements/',
                PartRequirements.as_view(),
                name='api-part-requirements',
            ),
            # Endpoint for duplicating a BOM for the specific Part
            path('bom-copy/', PartCopyBOM.as_view(), name='api-part-bom-copy'),
            # Endpoint for validating a BOM for the specific Part
            path(
                'bom-validate/', PartValidateBOM.as_view(), name='api-part-bom-validate'
            ),
            # Part metadata
            meta_path(Part),
            # Part pricing
            path('pricing/', PartPricingDetail.as_view(), name='api-part-pricing'),
            # Part detail endpoint
            path('', PartDetail.as_view(), name='api-part-detail'),
        ]),
    ),
    path('', PartList.as_view(), name='api-part-list'),
]

bom_api_urls = [
    path(
        'substitute/',
        include([
            # Detail view
            path(
                '&lt;int:pk&gt;/',
                include([
                    meta_path(BomItemSubstitute),
                    path(
                        '',
                        BomItemSubstituteDetail.as_view(),
                        name='api-bom-substitute-detail',
                    ),
                ]),
            ),
            # Catch all
            path('', BomItemSubstituteList.as_view(), name='api-bom-substitute-list'),
        ]),
    ),
    # BOM Item Detail
    path(
        '&lt;int:pk&gt;/',
        include([
            path('validate/', BomItemValidate.as_view(), name='api-bom-item-validate'),
            meta_path(BomItem),
            path('', BomDetail.as_view(), name='api-bom-item-detail'),
        ]),
    ),
    # Catch-all
    path('', BomList.as_view(), name='api-bom-list'),
]

  </file>
  <file path="src/backend/InvenTree/part/models.py">
"""Part database model definitions."""

from __future__ import annotations

import hashlib
import inspect
import math
import os
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, TypedDict, cast

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q, QuerySet, Sum, UniqueConstraint
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.db.utils import IntegrityError
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

import structlog
from django_cleanup import cleanup
from djmoney.contrib.exchange.exceptions import MissingRate
from djmoney.contrib.exchange.models import convert_money
from djmoney.money import Money
from mptt.managers import TreeManager
from mptt.models import TreeForeignKey

import common.currency
import common.models
import company.models
import InvenTree.conversion
import InvenTree.fields
import InvenTree.helpers
import InvenTree.models
import InvenTree.ready
import InvenTree.tasks
import part.helpers as part_helpers
import part.settings as part_settings
import report.mixins
import users.models
from build import models as BuildModels
from build.status_codes import BuildStatusGroups
from common.currency import currency_code_default
from common.icons import validate_icon
from common.settings import get_global_setting
from InvenTree import helpers, validators
from InvenTree.exceptions import log_error
from InvenTree.fields import InvenTreeURLField
from InvenTree.helpers import decimal2string, normalize
from order import models as OrderModels
from order.status_codes import (
    PurchaseOrderStatus,
    PurchaseOrderStatusGroups,
    SalesOrderStatusGroups,
    TransferOrderStatusGroups,
)
from stock import models as StockModels

logger = structlog.get_logger('inventree')


class PartCategory(
    InvenTree.models.PluginValidationMixin,
    InvenTree.models.InvenTreeParameterMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.PathStringMixin,
    InvenTree.models.InvenTreeTree,
):
    """PartCategory provides hierarchical organization of Part objects.

    Attributes:
        name: Name of this category
        parent: Parent category
        default_location: Default storage location for parts in this category or child categories
        default_keywords: Default keywords for parts created in this category
    """

    ITEM_PARENT_KEY = 'category'
    EXTRA_PATH_FIELDS = ['icon']
    IMPORT_ID_FIELDS = ['pathstring', 'name']

    class Meta:
        """Metaclass defines extra model properties."""

        verbose_name = _('Part Category')
        verbose_name_plural = _('Part Categories')

    def delete(self, *args, **kwargs):
        """Custom model deletion routine, which updates any child categories or parts.

        This must be handled within a transaction.atomic(), otherwise the tree structure is damaged
        """
        super().delete(
            delete_children=kwargs.get('delete_child_categories', False),
            delete_items=kwargs.get('delete_parts', False),
        )

    default_location = TreeForeignKey(
        'stock.StockLocation',
        related_name='default_categories',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_('Default Location'),
        help_text=_('Default location for parts in this category'),
    )

    structural = models.BooleanField(
        default=False,
        verbose_name=_('Structural'),
        help_text=_(
            'Parts may not be directly assigned to a structural category, '
            'but may be assigned to child categories.'
        ),
    )

    default_keywords = models.CharField(
        null=True,
        blank=True,
        max_length=250,
        verbose_name=_('Default keywords'),
        help_text=_('Default keywords for parts in this category'),
    )

    _icon = models.CharField(
        blank=True,
        null=True,
        max_length=100,
        verbose_name=_('Icon'),
        help_text=_('Icon (optional)'),
        validators=[validate_icon],
        db_column='icon',
    )

    @property
    def icon(self):
        """Return the icon associated with this PartCategory or the default icon."""
        if self._icon:
            return self._icon

        if default_icon := get_global_setting('PART_CATEGORY_DEFAULT_ICON', cache=True):
            return default_icon

        return ''

    @icon.setter
    def icon(self, value):
        """Setter for icon field."""
        default_icon = get_global_setting('PART_CATEGORY_DEFAULT_ICON', cache=True)

        # if icon is not defined previously and new value is default icon, do not save it
        if not self._icon and value == default_icon:
            return

        self._icon = value

    @staticmethod
    def get_api_url():
        """Return the API url associated with the PartCategory model."""
        return reverse('api-part-category-list')

    def get_absolute_url(self):
        """Return the web URL associated with the detail view for this PartCategory instance."""
        return helpers.pui_url(f'/part/category/{self.id}')

    def clean(self):
        """Custom clean action for the PartCategory model.

        Ensure that the structural parameter cannot get set if products already assigned to the category
        """
        if self.pk and self.structural and self.partcount(False, False) &gt; 0:
            raise ValidationError(
                _(
                    'You cannot make this part category structural because some parts '
                    'are already assigned to it!'
                )
            )
        super().clean()

    def get_parts(self, cascade=True) -&gt; set[Part]:
        """Return a queryset for all parts under this category.

        Args:
            cascade (bool, optional): If True, also look under subcategories. Defaults to True.

        Returns:
            set[Part]: All matching parts
        """
        if cascade:
            """Select any parts which exist in this category or any child categories."""
            queryset = Part.objects.filter(
                category__in=self.getUniqueChildren(include_self=True)
            )
        else:
            queryset = Part.objects.filter(category=self.pk)

        return queryset

    @property
    def item_count(self):
        """Return the number of parts contained in this PartCategory."""
        return self.partcount()

    def get_items(self, cascade=False):
        """Return a queryset containing the parts which exist in this category."""
        return self.get_parts(cascade=cascade)

    def partcount(self, cascade=True, active=False):
        """Return the total part count under this category (including children of child categories)."""
        query = self.get_parts(cascade=cascade)

        if active:
            query = query.filter(active=True)

        return query.count()

    def prefetch_parts_parameters(self, cascade=True):
        """Prefectch parts parameters."""
        return (
            self
            .get_parts(cascade=cascade)
            .prefetch_related('parameters_list', 'parameters_list__template')
            .all()
        )

    def get_unique_parameters(self, cascade=True, prefetch=None):
        """Get all unique parameter names for all parts from this category."""
        unique_parameters_names = []

        parts = prefetch or self.prefetch_parts_parameters(cascade=cascade)

        for part in parts:
            for parameter in part.parameters_list.all():
                parameter_name = parameter.template.name
                if parameter_name not in unique_parameters_names:
                    unique_parameters_names.append(parameter_name)

        return sorted(unique_parameters_names)

    def get_parts_parameters(self, cascade=True, prefetch=None):
        """Get all parameter names and values for all parts from this category."""
        category_parameters = []

        parts = prefetch or self.prefetch_parts_parameters(cascade=cascade)

        for part in parts:
            part_parameters = {
                'pk': part.pk,
                'name': part.name,
                'description': part.description,
            }
            # Add IPN only if it exists
            if part.IPN:
                part_parameters['IPN'] = part.IPN

            for parameter in part.parameters_list.all():
                parameter_name = parameter.template.name
                parameter_value = parameter.data
                part_parameters[parameter_name] = parameter_value

            category_parameters.append(part_parameters)

        return category_parameters

    @classmethod
    def get_parent_categories(cls):
        """Return tuple list of parent (root) categories."""
        # Get root nodes
        root_categories = cls.objects.filter(level=0)

        parent_categories = []
        for category in root_categories:
            parent_categories.append((category.id, category.name))

        return parent_categories

    def get_parameter_templates(self):
        """Return parameter templates associated to category."""
        prefetch = PartCategoryParameterTemplate.objects.prefetch_related(
            'category', 'parameter'
        )

        return prefetch.filter(category=self.id)

    def get_subscribers(self, include_parents: bool = True) -&gt; list[User]:
        """Return a list of users who subscribe to this PartCategory.

        Arguments:
            include_parents (bool): If True, include users who subscribe to parent categories.

        Returns:
            list[User]: List of users who subscribe to this category.
        """
        subscribers = set()

        if include_parents:
            cats = self.get_ancestors(include_self=True)
            queryset = PartCategoryStar.objects.filter(category__in=cats)
        else:
            queryset = PartCategoryStar.objects.filter(category=self)

        for result in queryset:
            subscribers.add(result.user)

        return list(subscribers)

    def is_starred_by(self, user, **kwargs):
        """Returns True if the specified user subscribes to this category."""
        return user in self.get_subscribers(**kwargs)

    def set_starred(self, user, status: bool, **kwargs) -&gt; None:
        """Set the "subscription" status of this PartCategory against the specified user."""
        if not user:
            return

        if self.is_starred_by(user, **kwargs) == status:
            return

        if status:
            PartCategoryStar.objects.create(category=self, user=user)
        else:
            # Note that this won't actually stop the user being subscribed,
            # if the user is subscribed to a parent category
            PartCategoryStar.objects.filter(category=self, user=user).delete()


def rename_part_image(instance, filename):
    """Function for renaming a part image file.

    Args:
        instance: Instance of a Part object
        filename: Name of original uploaded file

    Returns:
        Cleaned filename in format part_&lt;n&gt;_img
    """
    base = part_helpers.PART_IMAGE_DIR
    fname = os.path.basename(filename)

    return os.path.join(base, fname)


class PartCategoryParameterTemplate(InvenTree.models.InvenTreeMetadataModel):
    """A PartCategoryParameterTemplate creates a unique relationship between a PartCategory and a ParameterTemplate.

    Multiple ParameterTemplate instances can be associated to a PartCategory to drive a default list of parameter templates attached to a Part instance upon creation.

    Attributes:
        category: Reference to a single PartCategory object
        template: Reference to a single ParameterTemplate object
        default_value: The default value for the parameter in the context of the selected category
    """

    @staticmethod
    def get_api_url():
        """Return the API endpoint URL associated with the PartCategoryParameterTemplate model."""
        return reverse('api-part-category-parameter-list')

    class Meta:
        """Metaclass providing extra model definition."""

        verbose_name = _('Part Category Parameter Template')

        constraints = [
            UniqueConstraint(
                fields=['category', 'template'], name='unique_category_parameter_pair'
            )
        ]

    def __str__(self):
        """String representation of a PartCategoryParameterTemplate (admin interface)."""
        if self.default_value:
            return f'{self.category.name} | {self.template.name} | {self.default_value}'
        return f'{self.category.name} | {self.template.name}'

    def clean(self):
        """Validate this PartCategoryParameterTemplate instance.

        Checks the provided 'default_value', and (if not blank), ensure it is valid.
        """
        super().clean()

        self.default_value = (
            '' if self.default_value is None else str(self.default_value.strip())
        )

        if (
            self.default_value
            and get_global_setting(
                'PARAMETER_ENFORCE_UNITS', True, cache=False, create=False
            )
            and self.template.units
        ):
            try:
                InvenTree.conversion.convert_physical_value(
                    self.default_value, self.template.units
                )
            except ValidationError as e:
                raise ValidationError({'default_value': e.message})

    category = models.ForeignKey(
        PartCategory,
        on_delete=models.CASCADE,
        related_name='parameter_templates',
        verbose_name=_('Category'),
        help_text=_('Part Category'),
    )

    template = models.ForeignKey(
        common.models.ParameterTemplate,
        on_delete=models.CASCADE,
        related_name='part_categories',
    )

    default_value = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('Default Value'),
        help_text=_('Default Parameter Value'),
    )


class PartReportContext(report.mixins.BaseReportContext, TypedDict):
    """Report context for the Part model.

    Attributes:
        bom_items: Query set of all BomItem objects associated with the Part
        category: The PartCategory object associated with the Part
        description: The description field of the Part
        IPN: The IPN (internal part number) of the Part
        name: The name of the Part
        parameters: Dict object containing the parameters associated with the Part
        part: The Part object itself
        qr_data: Formatted QR code data for the Part
        qr_url: Generated URL for embedding in a QR code
        revision: The revision of the Part
        test_template_list: List of test templates associated with the Part
        test_templates: Dict object of test templates associated with the Part
    """

    bom_items: report.mixins.QuerySet[BomItem]
    category: PartCategory | None
    description: str
    IPN: str | None
    name: str
    parameters: dict[str, str]
    part: Part
    qr_data: str
    qr_url: str
    revision: str | None
    test_template_list: report.mixins.QuerySet[PartTestTemplate]
    test_templates: dict[str, PartTestTemplate]


@cleanup.ignore
class Part(
    InvenTree.models.PluginValidationMixin,
    InvenTree.models.InvenTreeParameterMixin,
    InvenTree.models.InvenTreeAttachmentMixin,
    InvenTree.models.InvenTreeBarcodeMixin,
    InvenTree.models.InvenTreeTagsMixin,
    InvenTree.models.InvenTreeNotesMixin,
    report.mixins.InvenTreeReportMixin,
    InvenTree.models.InvenTreeImageMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeTree,
):
    """The Part object represents an abstract part, the 'concept' of an actual entity.

    An actual physical instance of a Part is a StockItem which is treated separately.

    Parts can be used to create other parts (as part of a Bill of Materials or BOM).

    Attributes:
        name: Brief name for this part
        variant: Optional variant number for this part - Must be unique for the part name
        category: The PartCategory to which this part belongs
        description: Longer form description of the part
        keywords: Optional keywords for improving part search results
        IPN: Internal part number (optional)
        revision: Part revision
        is_template: If True, this part is a 'template' part
        link: Link to an external page with more information about this part (e.g. internal Wiki)
        image: Image of this part
        default_location: Where the item is normally stored (may be null)
        default_expiry: The default expiry duration for any StockItem instances of this part
        minimum_stock: Minimum preferred quantity to keep in stock
        maximum_stock: Maximum preferred quantity to keep in stock
        units: Units of measure for this part (default='pcs')
        salable: Can this part be sold to customers?
        assembly: Can this part be build from other parts?
        component: Can this part be used to make other parts?
        purchaseable: Can this part be purchased from suppliers?
        trackable: Trackable parts can have unique serial numbers assigned, etc, etc
        testable: Testable parts can have test results recorded against their stock items
        active: Is this part active? Parts are deactivated instead of being deleted
        locked: This part is locked and cannot be edited
        virtual: Is this part "virtual"? e.g. a software product or similar
        consumable: Is this part consumable, such as glue or a fastener?
        notes: Additional notes field for this part
        creation_date: Date that this part was added to the database
        creation_user: User who added this part to the database
        responsible_owner: Owner (either user or group) which is responsible for this part (optional)

    BOM (Bill of Materials) related attributes:
        bom_checksum: Checksum for the BOM of this part
        bom_validated: Boolean field indicating if the BOM is valid (checksum matches)
        bom_checked_by: User who last checked the BOM for this part
        bom_checked_date: Date when the BOM was last checked
    """

    NODE_PARENT_KEY = 'variant_of'
    IMAGE_RENAME = rename_part_image
    IMPORT_ID_FIELDS = ['IPN', 'name']

    objects = TreeManager()

    class Meta:
        """Metaclass defines extra model properties."""

        verbose_name = _('Part')
        verbose_name_plural = _('Parts')
        ordering = ['name']
        constraints = [
            UniqueConstraint(fields=['name', 'IPN', 'revision'], name='unique_part')
        ]

    class MPTTMeta:
        """MPTT Metaclass options."""

        # For legacy reasons the 'variant_of' field is used to indicate the MPTT parent
        parent_attr = 'variant_of'

    @staticmethod
    def get_api_url():
        """Return the list API endpoint URL associated with the Part model."""
        return reverse('api-part-list')

    def api_instance_filters(self):
        """Return API query filters for limiting field results against this instance."""
        return {'variant_of': {'exclude_tree': self.pk}}

    @classmethod
    def barcode_model_type_code(cls):
        """Return the associated barcode model type code for this model."""
        return 'PA'

    def report_context(self) -&gt; PartReportContext:
        """Return custom report context information."""
        return {
            'bom_items': cast(report.mixins.QuerySet['BomItem'], self.get_bom_items()),
            'category': self.category,
            'description': self.description,
            'IPN': self.IPN,
            'name': self.name,
            'parameters': self.parameters_map(),
            'part': self,
            'qr_data': self.barcode,
            'qr_url': self.get_absolute_url(),
            'revision': self.revision,
            'test_template_list': self.getTestTemplates(),
            'test_templates': self.getTestTemplateMap(),
        }

    def check_parameter_delete(self, parameter):
        """Custom delete check for Parameter instances associated with this Part."""
        if self.locked and get_global_setting('PART_ENABLE_LOCKING'):
            raise ValidationError(_('Cannot delete parameters of a locked part'))

    def check_parameter_save(self, parameter):
        """Custom save check for Parameter instances associated with this Part."""
        if self.locked and get_global_setting('PART_ENABLE_LOCKING'):
            raise ValidationError(_('Cannot modify parameters of a locked part'))

    def delete(self, **kwargs):
        """Custom delete method for the Part model.

        Prevents deletion of a Part if any of the following conditions are met:

        - The part is still active
        - The part is used in a BOM for a different part.
        """
        if self.locked and get_global_setting('PART_ENABLE_LOCKING'):
            raise ValidationError(_('Cannot delete this part as it is locked'))

        if self.active:
            raise ValidationError(_('Cannot delete this part as it is still active'))

        if not get_global_setting('PART_ALLOW_DELETE_FROM_ASSEMBLY', cache=False):
            if BomItem.objects.filter(sub_part=self).exists():
                raise ValidationError(
                    _('Cannot delete this part as it is used in an assembly')
                )

        super().delete()

    def save(self, *args, **kwargs):
        """Overrides the save function for the Part model.

        If the part image has been updated, then check if the "old" (previous) image is still used by another part.
        If not, it is considered "orphaned" and will be deleted.
        """
        _new = False
        if self.pk:
            try:
                previous = Part.objects.get(pk=self.pk)

                # Image has been changed
                if previous.image is not None and self.image != previous.image:
                    # Are there any (other) parts which reference the image?
                    n_refs = (
                        Part.objects
                        .filter(image=previous.image)
                        .exclude(pk=self.pk)
                        .count()
                    )

                    if n_refs == 0:
                        logger.info("Deleting unused image file '%s'", previous.image)
                        previous.image.delete(save=False)
            except Part.DoesNotExist:
                pass
        else:
            _new = True

        self.full_clean()

        super().save(*args, **kwargs)

        if _new:
            # Only run if the check was not run previously (due to not existing in the database)
            self.ensure_trackable()

    def __str__(self):
        """Return a string representation of the Part (for use in the admin interface)."""
        return f'{self.full_name} - {self.description}'

    def get_parts_in_bom(self, **kwargs):
        """Return a list of all parts in the BOM for this part.

        Takes into account substitutes, variant parts, and inherited BOM items
        """
        parts = set()

        for bom_item in self.get_bom_items(**kwargs):
            for part in bom_item.get_valid_parts_for_allocation():
                parts.add(part)

        return parts

    def check_if_part_in_bom(self, other_part, **kwargs):
        """Check if the other_part is in the BOM for *this* part.

        Note:
            - Accounts for substitute parts
            - Accounts for variant BOMs
        """
        return other_part in self.get_parts_in_bom(**kwargs)

    def check_add_to_bom(self, parent, raise_error=False, recursive=True):
        """Check if this Part can be added to the BOM of another part.

        This will fail if:

        a) The parent part is the same as this one
        b) The parent part exists in the same variant tree as this one
        c) The parent part is used in the BOM for *this* part
        d) The parent part is used in the BOM for any child parts under this one
        """
        result = True

        try:
            if self.pk == parent.pk:
                raise ValidationError({
                    'sub_part': _(
                        f"Part '{self}' cannot be used in BOM for '{parent}' (recursive)"
                    )
                })

            if self.tree_id == parent.tree_id:
                raise ValidationError({
                    'sub_part': _(
                        f"Part '{self}' cannot be used in BOM for '{parent}' (recursive)"
                    )
                })

            bom_items = self.get_bom_items()

            # Ensure that the parent part does not appear under any child BOM item!
            for item in bom_items.all():
                # Check for simple match
                if item.sub_part == parent:
                    raise ValidationError({
                        'sub_part': _(
                            f"Part '{parent}' is  used in BOM for '{self}' (recursive)"
                        )
                    })

                # And recursively check too
                if recursive:
                    result = result and item.sub_part.check_add_to_bom(
                        parent, recursive=True, raise_error=raise_error
                    )

        except ValidationError as e:
            if raise_error:
                raise e
            else:
                return False

        return result

    def validate_name(self, raise_error=True):
        """Validate the name field for this Part instance.

        This function is exposed to any Validation plugins, and thus can be customized.
        """
        from plugin import PluginMixinEnum, registry

        # Skip plugin validation checks during read-only management commands
        if not InvenTree.ready.isReadOnlyCommand():
            for plugin in registry.with_mixin(PluginMixinEnum.VALIDATION):
                # Run the name through each custom validator
                # If the plugin returns 'True' we will skip any subsequent validation

                try:
                    result = plugin.validate_part_name(self.name, self)
                    if result:
                        return
                except ValidationError as exc:
                    if raise_error:
                        raise ValidationError({'name': exc.message})
                except Exception:
                    log_error('validate_part_name', plugin=plugin.slug)

    def validate_ipn(self, raise_error=True):
        """Ensure that the IPN (internal part number) is valid for this Part".

        - Validation is handled by custom plugins
        - By default, no validation checks are performed
        """
        from plugin import PluginMixinEnum, registry

        # Skip plugin validation checks during read-only management commands
        if not InvenTree.ready.isReadOnlyCommand():
            for plugin in registry.with_mixin(PluginMixinEnum.VALIDATION):
                try:
                    result = plugin.validate_part_ipn(self.IPN, self)

                    if result:
                        # A "true" result force skips any subsequent checks
                        break
                except ValidationError as exc:
                    if raise_error:
                        raise ValidationError({'IPN': exc.message})
                except Exception:
                    log_error('validate_part_ipn', plugin=plugin.slug)

        # If we get to here, none of the plugins have raised an error
        pattern = get_global_setting('PART_IPN_REGEX', '', create=False).strip()

        if pattern:
            match = re.search(pattern, self.IPN)

            if match is None:
                raise ValidationError(_(f'IPN must match regex pattern {pattern}'))

    def validate_revision(self):
        """Check the 'revision' and 'revision_of' fields."""
        # Part cannot be a revision of itself
        if self.revision_of:
            if self.revision_of == self:
                raise ValidationError({
                    'revision_of': _('Part cannot be a revision of itself')
                })

            # If this part is a revision, it must have a revision code
            if not self.revision:
                raise ValidationError({
                    'revision': _(
                        'Revision code must be specified for a part marked as a revision'
                    )
                })

            if get_global_setting('PART_REVISION_ASSEMBLY_ONLY'):
                if not self.assembly or not self.revision_of.assembly:
                    raise ValidationError({
                        'revision_of': _(
                            'Revisions are only allowed for assembly parts'
                        )
                    })

            # Cannot have a revision of a "template" part
            if self.revision_of.is_template:
                raise ValidationError({
                    'revision_of': _('Cannot make a revision of a template part')
                })

            # parent part must point to the same template (via variant_of)
            if self.variant_of != self.revision_of.variant_of:
                raise ValidationError({
                    'revision_of': _('Parent part must point to the same template')
                })

    def validate_serial_number(
        self,
        serial: str,
        stock_item=None,
        check_duplicates=True,
        raise_error=False,
        **kwargs,
    ):
        """Validate a serial number against this Part instance.

        Note: This function is exposed to any Validation plugins, and thus can be customized.

        Any plugins which implement the 'validate_serial_number' method have three possible outcomes:

        - Decide the serial is objectionable and raise a django.core.exceptions.ValidationError
        - Decide the serial is acceptable, and return None to proceed to other tests
        - Decide the serial is acceptable, and return True to skip any further tests

        Arguments:
            serial: The proposed serial number
            stock_item: (optional) A StockItem instance which has this serial number assigned (e.g. testing for duplicates)
            check_duplicates: If True, checks for duplicate serial numbers in the database.
            raise_error: If False, and ValidationError(s) will be handled

        Returns:
            True if serial number is 'valid' else False

        Raises:
            ValidationError if serial number is invalid and raise_error = True
        """
        from plugin import PluginMixinEnum, registry

        serial = str(serial).strip()

        if not InvenTree.ready.isReadOnlyCommand():
            # First, throw the serial number against each of the loaded validation plugins
            for plugin in registry.with_mixin(PluginMixinEnum.VALIDATION):
                # Run the serial number through each custom validator
                # If the plugin returns 'True' we will skip any subsequent validation

                try:
                    result = False

                    if hasattr(plugin, 'validate_serial_number'):
                        signature = inspect.signature(plugin.validate_serial_number)

                        if 'stock_item' in signature.parameters:
                            # 2024-08-21: New method signature accepts a 'stock_item' parameter
                            result = plugin.validate_serial_number(
                                serial, self, stock_item=stock_item
                            )
                        else:
                            # Old method signature - does not accept a 'stock_item' parameter
                            result = plugin.validate_serial_number(serial, self)

                    if result is True:
                        return True
                except ValidationError as exc:
                    if raise_error:
                        # Re-throw the error
                        raise exc
                    else:
                        return False
                except Exception:
                    log_error('validate_serial_number', plugin=plugin.slug)

        """
        If we are here, none of the loaded plugins (if any) threw an error or exited early

        Now, we run the "default" serial number validation routine,
        which checks that the serial number is not duplicated
        """

        if not check_duplicates:
            return

        # from part.models import Part
        from stock.models import StockItem

        if get_global_setting('SERIAL_NUMBER_GLOBALLY_UNIQUE', False):
            # Serial number must be unique across *all* parts
            parts = Part.objects.all()
        else:
            # Serial number must only be unique across this part "tree"
            parts = Part.objects.filter(tree_id=self.tree_id)

        stock = StockItem.objects.filter(part__in=parts, serial=serial)

        if stock_item:
            # Exclude existing StockItem from query
            stock = stock.exclude(pk=stock_item.pk)

        if stock.exists():
            if raise_error:
                raise ValidationError(
                    _('Stock item with this serial number already exists')
                    + ': '
                    + serial
                )
            else:
                return False
        else:
            # This serial number is perfectly valid
            return True

    def find_conflicting_serial_numbers(self, serials: list) -&gt; list:
        """For a provided list of serials, return a list of those which are conflicting."""
        # from part.models import Part
        from stock.models import StockItem

        conflicts = []

        # First, check for raw conflicts based on efficient database queries
        if get_global_setting('SERIAL_NUMBER_GLOBALLY_UNIQUE', False):
            # Serial number must be unique across *all* parts
            parts = Part.objects.all()
        else:
            # Serial number must only be unique across this part "tree"
            parts = Part.objects.filter(tree_id=self.tree_id)

        items = StockItem.objects.filter(part__in=parts, serial__in=serials)
        items = items.order_by('serial_int', 'serial')

        for item in items:
            conflicts.append(item.serial)

        for serial in serials:
            if serial in conflicts:
                # Already found a conflict, no need to check further
                continue

            try:
                self.validate_serial_number(
                    serial, raise_error=True, check_duplicates=False
                )
            except ValidationError:
                # Serial number is invalid (as determined by plugin)
                conflicts.append(serial)

        return conflicts

    def get_latest_serial_number(self, allow_plugins=True):
        """Find the 'latest' serial number for this Part.

        Here we attempt to find the "highest" serial number which exists for this Part.
        There are a number of edge cases where this method can fail,
        but this is accepted to keep database performance at a reasonable level.

        Note: Serial numbers must be unique across an entire Part "tree",
        so we filter by the entire tree.

        Returns:
            The latest serial number specified for this part, or None
        """
        from plugin import PluginMixinEnum, registry

        if allow_plugins and not InvenTree.ready.isReadOnlyCommand():
            # Check with plugin system
            # If any plugin returns a non-null result, that takes priority
            for plugin in registry.with_mixin(PluginMixinEnum.VALIDATION):
                try:
                    result = plugin.get_latest_serial_number(self)
                    if result is not None:
                        return str(result)
                except Exception:
                    log_error('get_latest_serial_number', plugin=plugin.slug)

        # No plugin returned a result, so we will run the default query
        stock = (
            StockModels.StockItem.objects.all().exclude(serial=None).exclude(serial='')
        )

        # Generate a query for any stock items for this part variant tree with non-empty serial numbers
        if not get_global_setting('SERIAL_NUMBER_GLOBALLY_UNIQUE', False):
            # Serial numbers are unique across part trees
            stock = stock.filter(part__tree_id=self.tree_id)

        # There are no matching StockItem objects (skip further tests)
        if not stock.exists():
            return None

        # Sort in descending order
        stock = stock.order_by('-serial_int', '-serial', '-pk')

        # Return the first serial value
        return stock[0].serial

    def get_next_serial_number(self):
        """Return the 'next' serial number in sequence."""
        sn = self.get_latest_serial_number()

        return InvenTree.helpers.increment_serial_number(sn, self)

    @property
    @report.mixins.report_attribute()
    def full_name(self) -&gt; str:
        """Format a 'full name' for this Part based on the format PART_NAME_FORMAT defined in InvenTree settings."""
        return part_helpers.render_part_full_name(self)

    def get_absolute_url(self):
        """Return the web URL for viewing this part."""
        return helpers.pui_url(f'/part/{self.id}')

    def validate_unique(self, exclude=None):
        """Validate that this Part instance is 'unique'.

        Uniqueness is checked across the following (case insensitive) fields:
        - Name
        - IPN
        - Revision

        e.g. there can exist multiple parts with the same name, but only if
        they have a different revision or internal part number.
        """
        super().validate_unique(exclude)

        # User can decide whether duplicate IPN (Internal Part Number) values are allowed
        allow_duplicate_ipn = get_global_setting('PART_ALLOW_DUPLICATE_IPN')

        # Raise an error if an IPN is set, and it is a duplicate
        if self.IPN and not allow_duplicate_ipn:
            parts = Part.objects.filter(IPN__iexact=self.IPN)
            parts = parts.exclude(pk=self.pk)

            if parts.exists():
                raise ValidationError({
                    'IPN': _('Duplicate IPN not allowed in part settings')
                })

        if (
            self.revision_of
            and self.revision
            and (
                Part.objects
                .exclude(pk=self.pk)
                .filter(revision_of=self.revision_of, revision=self.revision)
                .exists()
            )
        ):
            raise ValidationError(_('Duplicate part revision already exists.'))

        # Ensure unique across (Name, revision, IPN) (as specified)
        if (self.revision or self.IPN) and (
            Part.objects
            .exclude(pk=self.pk)
            .filter(name=self.name, revision=self.revision, IPN=self.IPN)
            .exists()
        ):
            raise ValidationError(
                _('Part with this Name, IPN and Revision already exists.')
            )

    def clean(self):
        """Perform cleaning operations for the Part model.

        - Check if the PartCategory is not structural

        - Update trackable status:
            If this part is trackable, and it is used in the BOM
            for a parent part which is *not* trackable,
            then we will force the parent part to be trackable.
        """
        if self.category is not None and self.category.structural:
            raise ValidationError({
                'category': _('Parts cannot be assigned to structural part categories!')
            })

        # Check the 'revision' and 'revision_of' fields
        self.validate_revision()

        super().clean()

        # Strip IPN field
        if type(self.IPN) is str:
            self.IPN = self.IPN.strip()

        # Run custom validation for the IPN field
        self.validate_ipn()

        # Run custom validation for the name field
        self.validate_name()

        if self.pk:
            # Only run if the part already exists in the database
            self.ensure_trackable()

    def ensure_trackable(self):
        """Ensure that trackable is set correctly downstream."""
        if self.trackable:
            for part in self.get_used_in():
                if not part.trackable:
                    part.trackable = True
                    part.clean()
                    part.save()

    name = models.CharField(
        max_length=100, blank=False, help_text=_('Part name'), verbose_name=_('Name')
    )

    is_template = models.BooleanField(
        default=part_settings.part_template_default,
        verbose_name=_('Is Template'),
        help_text=_('Is this part a template part?'),
    )

    variant_of = models.ForeignKey(
        'part.Part',
        related_name='variants',
        null=True,
        blank=True,
        limit_choices_to={'is_template': True},
        on_delete=models.SET_NULL,
        help_text=_('Is this part a variant of another part?'),
        verbose_name=_('Variant Of'),
    )

    description = models.CharField(
        max_length=250,
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Part description (optional)'),
    )

    keywords = models.CharField(
        max_length=250,
        blank=True,
        null=True,
        verbose_name=_('Keywords'),
        help_text=_('Part keywords to improve visibility in search results'),
    )

    category = TreeForeignKey(
        PartCategory,
        related_name='parts',
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        verbose_name=_('Category'),
        help_text=_('Part category'),
    )

    IPN = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('IPN'),
        help_text=_('Internal Part Number'),
    )

    revision = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text=_('Part revision or version number'),
        verbose_name=_('Revision'),
    )

    revision_of = models.ForeignKey(
        'part.Part',
        related_name='revisions',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_('Is this part a revision of another part?'),
        verbose_name=_('Revision Of'),
    )

    link = InvenTreeURLField(
        blank=True,
        null=True,
        verbose_name=_('Link'),
        help_text=_('Link to external URL'),
        max_length=2000,
    )

    default_location = TreeForeignKey(
        'stock.StockLocation',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text=_('Where is this item normally stored?'),
        related_name='default_parts',
        verbose_name=_('Default Location'),
    )

    def get_default_location(self):
        """Get the default location for a Part (may be None).

        If the Part does not specify a default location,
        look at the Category this part is in.
        The PartCategory object may also specify a default stock location
        """
        if self.default_location:
            return self.default_location
        elif self.category:
            # Traverse up the category tree until we find a default location
            cats = self.category.get_ancestors(ascending=True, include_self=True)

            for cat in cats:
                if cat.default_location:
                    return cat.default_location

        # Default case - no default category found
        return None

    @property
    @report.mixins.report_attribute()
    def default_supplier(self) -&gt; Optional[company.models.SupplierPart]:
        """Return the default (primary) SupplierPart for this Part.

        This function is included for backwards compatibility,
        as the 'Part' model used to have a 'default_supplier' field which was a ForeignKey to SupplierPart.
        """
        return self.supplier_parts.filter(primary=True).first()

    default_expiry = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name=_('Default Expiry'),
        help_text=_('Expiry time (in days) for stock items of this part'),
    )

    minimum_stock = models.DecimalField(
        max_digits=19,
        decimal_places=6,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name=_('Minimum Stock'),
        help_text=_('Minimum allowed stock level'),
    )

    maximum_stock = models.DecimalField(
        max_digits=19,
        decimal_places=6,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name=_('Maximum Stock'),
        help_text=_('Maximum allowed stock level'),
    )

    units = models.CharField(
        max_length=20,
        default='',
        blank=True,
        null=True,
        verbose_name=_('Units'),
        help_text=_('Units of measure for this part'),
        validators=[validators.validate_physical_units],
    )

    assembly = models.BooleanField(
        default=part_settings.part_assembly_default,
        verbose_name=_('Assembly'),
        help_text=_('Can this part be built from other parts?'),
    )

    component = models.BooleanField(
        default=part_settings.part_component_default,
        verbose_name=_('Component'),
        help_text=_('Can this part be used to build other parts?'),
    )

    trackable = models.BooleanField(
        default=part_settings.part_trackable_default,
        verbose_name=_('Trackable'),
        help_text=_('Does this part have tracking for unique items?'),
    )

    testable = models.BooleanField(
        default=False,
        verbose_name=_('Testable'),
        help_text=_('Can this part have test results recorded against it?'),
    )

    purchaseable = models.BooleanField(
        default=part_settings.part_purchaseable_default,
        verbose_name=_('Purchaseable'),
        help_text=_('Can this part be purchased from external suppliers?'),
    )

    salable = models.BooleanField(
        default=part_settings.part_salable_default,
        verbose_name=_('Salable'),
        help_text=_('Can this part be sold to customers?'),
    )

    active = models.BooleanField(
        default=True, verbose_name=_('Active'), help_text=_('Is this part active?')
    )

    locked = models.BooleanField(
        default=False,
        verbose_name=_('Locked'),
        help_text=_('Locked parts cannot be edited'),
    )

    virtual = models.BooleanField(
        default=part_settings.part_virtual_default,
        verbose_name=_('Virtual'),
        help_text=_('Is this a virtual part, such as a software product or license?'),
    )

    consumable = models.BooleanField(
        default=False,
        verbose_name=_('Consumable'),
        help_text=_('Is this part consumable, such as glue or a fastener?'),
    )

    bom_validated = models.BooleanField(
        default=False,
        verbose_name=_('BOM Validated'),
        help_text=_('Is the BOM for this part valid?'),
    )

    bom_checksum = models.CharField(
        max_length=128,
        blank=True,
        verbose_name=_('BOM checksum'),
        help_text=_('Stored BOM checksum'),
    )

    bom_checked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_('BOM checked by'),
        related_name='boms_checked',
    )

    bom_checked_date = models.DateField(
        blank=True, null=True, verbose_name=_('BOM checked date')
    )

    creation_date = models.DateField(
        auto_now_add=True,
        editable=False,
        blank=True,
        null=True,
        verbose_name=_('Creation Date'),
    )

    creation_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_('Creation User'),
        related_name='parts_created',
    )

    responsible_owner = models.ForeignKey(
        users.models.Owner,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_('Responsible'),
        help_text=_('Owner responsible for this part'),
        related_name='parts_responsible',
    )

    @property
    def category_path(self) -&gt; str:
        """Return the category path of this Part instance."""
        if self.category:
            return self.category.pathstring
        return ''

    @property
    @report.mixins.report_attribute()
    def available_stock(self) -&gt; Decimal:
        """Return the total available stock.

        - This subtracts stock which is already allocated to builds
        """
        total = self.total_stock
        total -= self.allocation_count()

        return max(total, 0)

    def requiring_build_orders(self, include_variants: bool = True):
        """Return list of outstanding build orders which require this part.

        Arguments:
            include_variants: If True, include variants of this part in the calculation
        """
        # List parts that this part is required for

        if include_variants:
            # If we are including variants, get all parts in the variant tree
            parts = list(self.get_descendants(include_self=True))
        else:
            parts = [self]

        used_in_parts = set()

        for part in parts:
            # Get all assemblies which use this part
            used_in_parts.update(part.get_used_in())

        # Now, get a list of outstanding build orders which require this part
        builds = BuildModels.Build.objects.filter(
            part__in=list(used_in_parts), status__in=BuildStatusGroups.ACTIVE_CODES
        )

        return builds

    def required_build_order_quantity(self, include_variants: bool = True):
        """Return the quantity of this part required for active build orders.

        Arguments:
            include_variants: If True, include variants of this part in the calculation
        """
        # List active build orders which reference this part
        builds = self.requiring_build_orders(include_variants=include_variants)

        quantity = 0

        if include_variants:
            matching_parts = list(self.get_descendants(include_self=True))
        else:
            matching_parts = [self]

        # Cache the BOM items that we query
        # Keep a dict of part ID to BOM items
        cached_bom_items: dict = {}

        for build in builds:
            if build.part.pk not in cached_bom_items:
                # Get the BOM items for this part
                bom_items = build.part.get_bom_items().filter(
                    sub_part__in=matching_parts
                )
                cached_bom_items[build.part.pk] = bom_items
            else:
                bom_items = cached_bom_items[build.part.pk]

            # Match BOM item to build
            for bom_item in bom_items:
                build_line = build.build_lines.filter(bom_item=bom_item).first()

                if not build_line:
                    continue

                line_quantity = max(0, build_line.quantity - build_line.consumed)
                quantity += line_quantity

        return quantity

    def requiring_sales_orders(self, include_variants: bool = True):
        """Return a list of sales orders which require this part.

        Arguments:
            include_variants: If True, include variants of this part in the calculation
        """
        orders = set()

        if include_variants:
            parts = list(self.get_descendants(include_self=True))
        else:
            parts = [self]

        # Get a list of line items for open orders which match this part
        open_lines = OrderModels.SalesOrderLineItem.objects.filter(
            order__status__in=SalesOrderStatusGroups.OPEN, part__in=parts
        )

        for line in open_lines:
            orders.add(line.order)

        return orders

    def required_sales_order_quantity(self, include_variants: bool = True):
        """Return the quantity of this part required for active sales orders.

        Arguments:
            include_variants: If True, include variants of this part in the calculation
        """
        if include_variants:
            parts = list(self.get_descendants(include_self=True))
        else:
            parts = [self]

        # Get a list of line items for open orders which match this part
        open_lines = OrderModels.SalesOrderLineItem.objects.filter(
            order__status__in=SalesOrderStatusGroups.OPEN, part__in=parts
        )

        quantity = 0

        for line in open_lines:
            # Determine the quantity "remaining" to be shipped out

            if not line:
                continue

            remaining = max(line.quantity - line.shipped, 0)
            quantity += remaining

        return quantity

    def required_order_quantity(self, include_variants: bool = True):
        """Return total required to fulfil orders."""
        return self.required_build_order_quantity(
            include_variants=include_variants
        ) + self.required_sales_order_quantity(include_variants=include_variants)

    @property
    def quantity_to_order(self):
        """Return the quantity needing to be ordered for this part.

        Here, an "order" could be one of:
        - Build Order
        - Sales Order

        To work out how many we need to order:

        Stock on hand = self.total_stock
        Required for orders = self.required_order_quantity()
        Currently on order = self.on_order
        Currently building = self.quantity_being_built
        """
        # Total requirement
        required = self.required_order_quantity()

        # Subtract stock levels
        required -= max(self.total_stock, self.minimum_stock)

        # Subtract quantity on order
        required -= self.on_order

        # Subtract quantity being built
        required -= self.quantity_being_built

        return max(required, 0)

    @property
    def net_stock(self):
        """Return the 'net' stock.

        It takes into account:
        - Stock on hand (total_stock)
        - Stock on order (on_order)
        - Stock allocated (allocation_count)

        This number (unlike 'available_stock') can be negative.
        """
        return self.total_stock - self.allocation_count() + self.on_order

    def get_subscribers(
        self, include_variants: bool = True, include_categories: bool = True
    ) -&gt; list[User]:
        """Return a list of users who are 'subscribed' to this part.

        Arguments:
            include_variants: If True, include users who are subscribed to a variant part
            include_categories: If True, include users who are subscribed to the category

        Returns:
            list[User]: A list of users who are subscribed to this part

        A user may 'subscribe' to this part in the following ways:

        a) Subscribing to the part instance directly
        b) Subscribing to a template part "above" this part (if it is a variant)
        c) Subscribing to the part category that this part belongs to
        d) Subscribing to a parent category of the category in c)
        """
        subscribers = set()

        # Start by looking at direct subscriptions to a Part model
        queryset = PartStar.objects.all()

        if include_variants:
            queryset = queryset.filter(part__in=self.get_ancestors(include_self=True))
        else:
            queryset = queryset.filter(part=self)

        for star in queryset:
            subscribers.add(star.user)

        if include_categories and self.category:
            for sub in self.category.get_subscribers():
                subscribers.add(sub)

        return list(subscribers)

    def is_starred_by(self, user, **kwargs):
        """Return True if the specified user subscribes to this part."""
        return user in self.get_subscribers(**kwargs)

    def set_starred(self, user, status, **kwargs):
        """Set the "subscription" status of this Part against the specified user."""
        if not user:
            return

        # Already subscribed?
        if self.is_starred_by(user, **kwargs) == status:
            return

        if status:
            PartStar.objects.create(part=self, user=user)
        else:
            # Note that this won't actually stop the user being subscribed,
            # if the user is subscribed to a parent part or category
            PartStar.objects.filter(part=self, user=user).delete()

    @property
    @report.mixins.report_attribute()
    def can_build(self) -&gt; Decimal:
        """Return the number of units that can be build with available stock."""
        import part.filters

        # If this part does NOT have a BOM, result is simply the currently available stock
        if not self.has_bom:
            return 0

        # Ignore virtual parts when calculating the "can_build" quantity
        queryset = self.get_bom_items(include_virtual=False)

        # Ignore 'consumable' BOM items for this calculation
        queryset = queryset.filter(BomItem.consumable_filter(consumable=False))

        # Annotate the queryset with the 'can_build' quantity
        queryset = part.filters.annotate_bom_item_can_build(queryset)

        can_build_quantity = None

        for value in queryset.values_list('can_build', flat=True):
            if can_build_quantity is None:
                can_build_quantity = value
            else:
                can_build_quantity = min(can_build_quantity, value)

        if can_build_quantity is None:
            # No BOM items, or no items which can be built
            return 0

        return int(max(can_build_quantity, 0))

    @property
    def active_builds(self) -&gt; int:
        """Return a list of outstanding builds.

        Builds marked as 'complete' or 'cancelled' are ignored
        """
        return self.builds.filter(status__in=BuildStatusGroups.ACTIVE_CODES)

    @property
    @report.mixins.report_attribute()
    def quantity_being_built(self) -&gt; Decimal:
        """Return the current number of parts currently being built.

        Note: This is the total quantity of Build orders, *not* the number of build outputs.
              In this fashion, it is the "projected" quantity of builds
        """
        builds = BuildModels.Build.objects.filter(
            status__in=BuildStatusGroups.ACTIVE_CODES
        )

        # We are including variants, get all parts in the variant tree
        builds = builds.filter(part__in=self.get_descendants(include_self=True))

        quantity = 0

        for build in builds:
            # The remaining items in the build
            quantity += build.remaining

        return quantity

    @property
    def quantity_in_production(self, include_variants: bool = True) -&gt; Decimal:
        """Quantity of this part currently actively in production.

        Arguments:
            include_variants: If True, include variants of this part in the calculation

        Note: This may return a different value to `quantity_being_built`
        """
        quantity = 0

        items = StockModels.StockItem.objects.filter(
            is_building=True, build__status__in=BuildStatusGroups.ACTIVE_CODES
        )

        if include_variants:
            # If we are including variants, get all parts in the variant tree
            items = items.filter(part__in=self.get_descendants(include_self=True))
        else:
            # Only look at this part
            items = items.filter(part=self)

        for item in items:
            # The remaining items in the build
            quantity += item.quantity

        return quantity

    def build_order_allocations(self, **kwargs):
        """Return all 'BuildItem' objects which allocate this part to Build objects."""
        include_variants = kwargs.get('include_variants', True)

        queryset = BuildModels.BuildItem.objects.all()

        if include_variants:
            variants = self.get_descendants(include_self=True)
            queryset = queryset.filter(stock_item__part__in=variants)
        else:
            queryset = queryset.filter(stock_item__part=self)

        return queryset

    def build_order_allocation_count(self, **kwargs):
        """Return the total amount of this part allocated to build orders."""
        query = self.build_order_allocations(**kwargs).aggregate(
            total=Coalesce(
                Sum('quantity', output_field=models.DecimalField()),
                0,
                output_field=models.DecimalField(),
            )
        )

        return query['total']

    def sales_order_allocations(self, **kwargs):
        """Return all sales-order-allocation objects which allocate this part to a SalesOrder."""
        include_variants = kwargs.get('include_variants', True)

        queryset = OrderModels.SalesOrderAllocation.objects.all()

        if include_variants:
            # Include allocations for all variants
            variants = self.get_descendants(include_self=True)
            queryset = queryset.filter(item__part__in=variants)
        else:
            # Only look at this part
            queryset = queryset.filter(item__part=self)

        # Default behaviour is to only return *pending* allocations
        pending = kwargs.get('pending', True)

        if pending is True:
            # Look only for 'open' orders which have not shipped
            queryset = queryset.filter(
                line__order__status__in=SalesOrderStatusGroups.OPEN,
                shipment__shipment_date=None,
            )
        elif pending is False:
            # Look only for 'closed' orders or orders which have shipped
            queryset = queryset.exclude(
                line__order__status__in=SalesOrderStatusGroups.OPEN,
                shipment__shipment_date=None,
            )

        return queryset

    def sales_order_allocation_count(self, **kwargs):
        """Return the total quantity of this part allocated to sales orders."""
        query = self.sales_order_allocations(**kwargs).aggregate(
            total=Coalesce(
                Sum('quantity', output_field=models.DecimalField()),
                0,
                output_field=models.DecimalField(),
            )
        )

        return query['total']

    def transfer_order_allocations(self, **kwargs):
        """Return all transfer-order-allocation objects which allocate this part to a TransferOrder."""
        include_variants = kwargs.get('include_variants', True)

        queryset = OrderModels.TransferOrderAllocation.objects.all()

        if include_variants:
            # Include allocations for all variants
            variants = self.get_descendants(include_self=True)
            queryset = queryset.filter(item__part__in=variants)
        else:
            # Only look at this part
            queryset = queryset.filter(item__part=self)

        # Default behaviour is to only return *pending* allocations
        pending = kwargs.get('pending', True)

        if pending is True:
            # Look only for 'open' orders
            queryset = queryset.filter(
                line__order__status__in=TransferOrderStatusGroups.OPEN
            )
        elif pending is False:
            # Look only for 'closed' orders
            queryset = queryset.exclude(
                line__order__status__in=TransferOrderStatusGroups.OPEN
            )

        return queryset

    def transfer_order_allocation_count(self, **kwargs):
        """Return the total quantity of this part allocated to transfer orders."""
        query = self.transfer_order_allocations(**kwargs).aggregate(
            total=Coalesce(
                Sum('quantity', output_field=models.DecimalField()),
                0,
                output_field=models.DecimalField(),
            )
        )

        return query['total']

    def allocation_count(self, **kwargs):
        """Return the total quantity of stock allocated for this part, against build orders, sales orders, and transfer orders."""
        if self.id is None:
            # If this instance has not been saved, foreign-key lookups will fail
            return 0

        return sum([
            self.build_order_allocation_count(**kwargs),
            self.sales_order_allocation_count(**kwargs),
            # For now, stock allocated to a transfer order will not impact its availability
            # self.transfer_order_allocation_count(**kwargs),
        ])

    def stock_entries(
        self, include_variants=True, include_external=True, in_stock=None, location=None
    ):
        """Return all stock entries for this Part.

        Arguments:
            include_variants: If True, include stock entries for all part variants
            include_external: If True, include stock entries which are in 'external' locations
            in_stock: If True, filter by stock entries which are 'in stock'
            location: If set, filter by stock entries in the specified location
        """
        if include_variants:
            query = StockModels.StockItem.objects.filter(
                part__in=self.get_descendants(include_self=True)
            )
        else:
            query = self.stock_items

        if in_stock is True:
            query = query.filter(StockModels.StockItem.IN_STOCK_FILTER)
        elif in_stock is False:
            query = query.exclude(StockModels.StockItem.IN_STOCK_FILTER)

        if include_external is False:
            # Exclude stock entries which are not 'internal'
            query = query.filter(location__external=False)

        if location:
            locations = location.get_descendants(include_self=True)
            query = query.filter(location__in=locations)

        return query

    def get_stock_count(self, include_variants=True):
        """Return the total "in stock" count for this part."""
        entries = self.stock_entries(in_stock=True, include_variants=include_variants)

        query = entries.aggregate(t=Coalesce(Sum('quantity'), Decimal(0)))

        return query['t']

    @property
    @report.mixins.report_attribute()
    def total_stock(self) -&gt; Decimal:
        """Return the total stock quantity for this part.

        - Part may be stored in multiple locations
        - If this part is a "template" (variants exist) then these are counted too
        """
        return self.get_stock_count(include_variants=True)

    def get_bom_item_filter(
        self, include_inherited: bool = True, include_virtual: bool = True
    ):
        """Returns a query filter for all BOM items associated with this Part.

        Arguments:
            include_inherited: If True, include BomItem entries defined for parent parts
            include_virtual: If True, include BomItem entries which are virtual

        There are some considerations:

        a) BOM items can be defined against *this* part
        b) BOM items can be inherited from a *parent* part

        We will construct a filter to grab *all* the BOM items!

        Note: This does *not* return a queryset, it returns a Q object,
              which can be used by some other query operation!
              Because we want to keep our code DRY!
        """
        bom_filter = Q(part=self)

        if include_inherited:
            # We wish to include parent parts

            parents = self.get_ancestors(include_self=False)

            # There are parents available
            if parents.exists():
                parent_filter = Q(part__in=parents, inherited=True)

                # OR the filters together
                bom_filter |= parent_filter

        if not include_virtual:
            bom_filter &amp;= Q(sub_part__virtual=False)

        return bom_filter

    def get_bom_items(
        self, include_inherited: bool = True, include_virtual: bool = True
    ) -&gt; QuerySet[BomItem]:
        """Return a queryset containing all BOM items for this part.

        Arguments:
            include_inherited (bool): If set, include BomItem entries defined for parent parts
            include_virtual (bool): If set, include BomItem entries which are virtual parts
        """
        queryset = BomItem.objects.filter(
            self.get_bom_item_filter(
                include_inherited=include_inherited, include_virtual=include_virtual
            )
        )

        return queryset.prefetch_related('part', 'sub_part')

    def get_installed_part_options(
        self, include_inherited: bool = True, include_variants: bool = True
    ):
        """Return a set of all Parts which can be "installed" into this part, based on the BOM.

        Arguments:
            include_inherited (bool): If set, include BomItem entries defined for parent parts
            include_variants (bool): If set, include variant parts for BomItems which allow variants
        """
        parts = set()

        for bom_item in self.get_bom_items(include_inherited=include_inherited):
            if include_variants and bom_item.allow_variants:
                for part in bom_item.sub_part.get_descendants(include_self=True):
                    parts.add(part)
            else:
                parts.add(bom_item.sub_part)

        return parts

    def get_used_in_bom_item_filter(
        self, include_variants=True, include_substitutes=True
    ):
        """Return a BomItem queryset which returns all BomItem instances which refer to *this* part.

        As the BOM allocation logic is somewhat complicated, there are some considerations:

        A) This part may be directly specified in a BomItem instance
        B) This part may be a *variant* of a part which is directly specified in a BomItem instance
        C) This part may be a *substitute* for a part which is directly specified in a BomItem instance

        So we construct a query for each case, and combine them...
        """
        # Cache all *parent* parts
        try:
            parents = self.get_ancestors(include_self=False)
        except ValueError:
            # If get_ancestors() fails, then this part is not saved yet
            parents = []

        # Case A: This part is directly specified in a BomItem (we always use this case)
        query = Q(sub_part=self)

        if include_variants:
            # Case B: This part is a *variant* of a part which is specified in a BomItem which allows variants
            query |= Q(allow_variants=True, sub_part__in=parents)

        # Case C: This part is a *substitute* of a part which is directly specified in a BomItem
        if include_substitutes:
            # Grab a list of BomItem substitutes which reference this part
            substitutes = self.substitute_items.all()

            query |= Q(pk__in=[substitute.bom_item.pk for substitute in substitutes])

        return query

    def get_used_in(self, include_inherited=True, include_substitutes=True):
        """Return a list containing all parts this part is used in.

        Includes consideration of inherited BOMs
        """
        # Grab a queryset of all BomItem objects which "require" this part
        bom_items = BomItem.objects.filter(
            self.get_used_in_bom_item_filter(include_substitutes=include_substitutes)
        )

        # Iterate through the returned items and construct a set of
        parts = set()

        for bom_item in bom_items:
            if bom_item.part in parts:
                continue

            parts.add(bom_item.part)

            # Include inherited BOMs?
            if include_inherited and bom_item.inherited:
                try:
                    descendants = bom_item.part.get_descendants(include_self=False)
                except ValueError:
                    # This part is not saved yet
                    descendants = []

                for variant in descendants:
                    parts.add(variant)

        return list(parts)

    @property
    def has_bom(self) -&gt; bool:
        """Return True if this Part instance has any BOM items."""
        return self.get_bom_items().exists()

    def get_trackable_parts(self):
        """Return a queryset of all trackable parts in the BOM for this part."""
        queryset = self.get_bom_items()
        queryset = queryset.filter(sub_part__trackable=True)

        return queryset

    @property
    def has_trackable_parts(self) -&gt; bool:
        """Return True if any parts linked in the Bill of Materials are trackable.

        This is important when building the part.
        """
        return self.get_trackable_parts().exists()

    @property
    def bom_count(self) -&gt; int:
        """Return the number of items contained in the BOM for this part."""
        return self.get_bom_items().count()

    @property
    def used_in_count(self) -&gt; int:
        """Return the number of part BOMs that this part appears in."""
        return len(self.get_used_in())

    def get_bom_hash(self) -&gt; str:
        """Return a checksum hash for the BOM for this part.

        Used to determine if the BOM has changed (and needs to be signed off!)
        The hash is calculated by hashing each line item in the BOM. Returns a string representation of a hash object which can be compared with a stored value
        """
        result_hash = hashlib.md5(str(self.id).encode())

        # List *all* BOM items (including inherited ones!)
        # Note: We must order the BOM items in a consistent way, otherwise the hash will change if the order of the items changes
        bom_items = (
            self
            .get_bom_items()
            .all()
            .prefetch_related('part', 'sub_part')
            .order_by('pk')
        )

        for item in bom_items:
            result_hash.update(str(item.get_item_hash()).encode())

        return str(result_hash.digest())

    def is_bom_valid(self) -&gt; bool:
        """Check if the BOM is 'valid'.

        To be "valid", the part must:
        - Have a stored "bom_checksum" value
        - The stored "bom_checksum" must match the calculated checksum.

        Returns:
            bool: True if the BOM is valid, False otherwise
        """
        if not self.bom_checksum or not self.bom_checked_date:
            # If there is no BOM checksum, then the BOM is not valid
            return False

        return self.get_bom_hash() == self.bom_checksum

    @transaction.atomic
    def validate_bom(self, user, valid: bool = True):
        """Validate the BOM (mark the BOM as validated by the given User.

        Arguments:
            user: User who is validating the BOM
            valid: If True, mark the BOM as valid (default=True)

        - Calculates and stores the hash for the BOM
        - Saves the current date and the checking user
        """
        # Validate each line item, ignoring inherited ones
        bom_items = self.get_bom_items(include_inherited=False).prefetch_related(
            'part', 'sub_part'
        )

        if valid:
            for item in bom_items:
                item.validate_hash(valid=True)

        self.bom_validated = valid
        self.bom_checksum = self.get_bom_hash() if valid else ''
        self.bom_checked_by = user
        self.bom_checked_date = InvenTree.helpers.current_date()

        self.save()

    @transaction.atomic
    def clear_bom(self):
        """Clear the BOM items for the part (delete all BOM lines).

        Note: Does *NOT* delete inherited BOM items!
        """
        import part.tasks as part_tasks

        self.bom_items.all().delete()

        # Offload task to re-validate the BOM for this assembly
        InvenTree.tasks.offload_task(part_tasks.check_bom_valid, self.pk, group='part')

    def getRequiredParts(self, recursive=False, parts=None):
        """Return a list of parts required to make this part (i.e. BOM items).

        Args:
            recursive: If True iterate down through sub-assemblies
            parts: Set of parts already found (to prevent recursion issues)
        """
        if parts is None:
            parts = set()

        bom_items = self.get_bom_items().prefetch_related('sub_part')

        for bom_item in bom_items:
            sub_part = bom_item.sub_part

            if sub_part not in parts:
                parts.add(sub_part)

                if recursive:
                    sub_part.getRequiredParts(recursive=True, parts=parts)

        return parts

    @property
    def supplier_count(self):
        """Return the number of supplier parts available for this part."""
        return self.supplier_parts.count()

    def update_pricing(self):
        """Recalculate cached pricing for this Part instance."""
        self.pricing.update_pricing()

    @property
    def pricing(self):
        """Return the PartPricing information for this Part instance.

        If there is no PartPricing database entry defined for this Part,
        it will first be created, and then returned.
        """
        try:
            pricing = PartPricing.objects.get(part=self)
        except PartPricing.DoesNotExist:
            pricing = PartPricing(part=self)

        return pricing

    def schedule_pricing_update(
        self, create: bool = False, force: bool = False, refresh: bool = True
    ):
        """Helper function to schedule a pricing update.

        Importantly, catches any errors which may occur during deletion of related objects,
        in particular due to post_delete signals.

        Ref: https://github.com/inventree/InvenTree/pull/3986

        Arguments:
            create: Whether or not a new PartPricing object should be created if it does not already exist
            force: If True, force the pricing to be updated even auto pricing is disabled
            refresh: If True, refresh the PartPricing object from the database
        """
        if not force and not get_global_setting(
            'PRICING_AUTO_UPDATE', backup_value=True
        ):
            return

        if refresh:
            try:
                self.refresh_from_db()
            except Part.DoesNotExist:
                return

        try:
            pricing = self.pricing

            if create or pricing.pk:
                pricing.schedule_for_update(refresh=refresh)
        except IntegrityError:
            # If this part instance has been deleted,
            # some post-delete or post-save signals may still be fired
            # which can cause issues down the track
            pass

    base_cost = models.DecimalField(
        max_digits=19,
        decimal_places=6,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name=_('base cost'),
        help_text=_('Minimum charge (e.g. stocking fee)'),
    )

    multiple = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name=_('multiple'),
        help_text=_('Sell multiple'),
    )

    @transaction.atomic
    def copy_bom_from(self, other, clear: bool = True, **kwargs):
        """Copy the BOM from another part.

        Args:
            other: The part to copy the BOM from
            clear (bool, optional): Remove existing BOM items first. Defaults to True.
        """
        # Ignore if the other part is actually this part?
        if other == self:
            return

        if clear:
            # Remove existing BOM items
            # Note: Inherited BOM items are *not* deleted!
            self.bom_items.all().delete()

        # List of "ancestor" parts above this one
        my_ancestors = self.get_ancestors(include_self=False)

        raise_error = not kwargs.get('skip_invalid', True)

        include_inherited = kwargs.get('include_inherited', False)

        # Should substitute parts be duplicated?
        copy_substitutes = kwargs.get('copy_substitutes', True)

        # Copy existing BOM items from another part
        # Note: Inherited BOM Items will *not* be duplicated!!
        for bom_item in other.get_bom_items(include_inherited=include_inherited).all():
            # If this part already has a BomItem pointing to the same sub-part,
            # delete that BomItem from this part first!

            # Ignore invalid BomItem objects
            if not bom_item.part or not bom_item.sub_part:
                continue

            # Ignore ancestor parts which are inherited
            if bom_item.part in my_ancestors and bom_item.inherited:
                continue

            # Skip (or throw error) if BomItem is not valid
            if not bom_item.sub_part.check_add_to_bom(self, raise_error=raise_error):
                continue

            # Obtain a list of direct substitute parts against this BomItem
            substitutes = BomItemSubstitute.objects.filter(bom_item=bom_item)

            # Construct a new BOM item
            bom_item.part = self
            bom_item.pk = None

            bom_item.save()
            bom_item.refresh_from_db()

            if copy_substitutes:
                for sub in substitutes:
                    # Duplicate the substitute (and point to the *new* BomItem object)
                    sub.pk = None
                    sub.bom_item = bom_item
                    sub.save()

    @transaction.atomic
    def copy_tests_from(self, other: Part, **kwargs) -&gt; None:
        """Copy all test templates from another Part instance.

        Note: We only copy the direct test templates, not ones inherited from parent parts.
        """
        templates = []
        parts = self.get_ancestors(include_self=True)

        # Prevent tests from being created for non-testable parts
        if not self.testable:
            return

        for template in other.test_templates.all():
            # Skip if a test template already exists for this part / key combination
            if PartTestTemplate.objects.filter(
                key=template.key, part__in=parts
            ).exists():
                continue

            template.pk = None
            template.part = self
            templates.append(template)

        if len(templates) &gt; 0:
            PartTestTemplate.objects.bulk_create(templates, batch_size=250)

    @transaction.atomic
    def copy_category_parameters(self, category: PartCategory):
        """Copy parameter templates from the specified PartCategory.

        This function is normally called when the Part is first created.
        """
        from common.models import Parameter

        categories = category.get_ancestors(include_self=True)

        category_templates = PartCategoryParameterTemplate.objects.filter(
            category__in=categories
        ).order_by('-category__level')

        template_ids = set()
        parameters = []
        content_type = ContentType.objects.get_for_model(Part)

        for category_template in category_templates:
            # First ensure that the part doesn't have that parameter
            if self.parameters_list.filter(
                template=category_template.template
            ).exists():
                continue

            # Ensure we do not create duplicate parameters if multiple categories have the same template
            if category_template.template.pk in template_ids:
                continue

            # Skip templates which enforce a uniqueness requirement - applying the same
            # default value to every part in the category would create conflicting values
            if (
                category_template.template.unique
                != common.models.ParameterTemplate.UniqueOptions.NONE
            ):
                continue

            template_ids.add(category_template.template.pk)

            parameters.append(
                Parameter(
                    template=category_template.template,
                    model_type=content_type,
                    model_id=self.pk,
                    data=category_template.default_value,
                )
            )

        Parameter.objects.bulk_create(parameters, batch_size=250)

    def getTestTemplates(
        self, required=None, include_parent: bool = True, enabled=None
    ) -&gt; QuerySet[PartTestTemplate]:
        """Return a list of all test templates associated with this Part.

        These are used for validation of a StockItem.


        Args:
            required (bool, optional): Filter templates by whether they are required. Defaults to None.
            include_parent (bool, optional): Include templates from parent parts. Defaults to True.
            enabled (bool, optional): Filter templates by their enabled status. Defaults to None.

        Returns:
            QuerySet: A queryset of matching test templates.
        """
        if include_parent:
            tests = PartTestTemplate.objects.filter(
                part__in=self.get_ancestors(include_self=True)
            )
        else:
            tests = self.test_templates

        if required is not None:
            tests = tests.filter(required=required)

        if enabled is not None:
            tests = tests.filter(enabled=enabled)

        return tests

    def getTestTemplateMap(self, **kwargs):
        """Return a map of all test templates associated with this Part."""
        templates = {}

        for template in self.getTestTemplates(**kwargs):
            templates[template.key] = template

        return templates

    def getRequiredTests(self, include_parent=True, enabled=True):
        """Return the tests which are required by this part.

        Arguments:
            include_parent: If True, include tests which are defined for parent parts
            enabled: If set (either True or False), filter by template "enabled" status
        """
        return self.getTestTemplates(
            required=True, enabled=enabled, include_parent=include_parent
        )

    def sales_orders(self):
        """Return a list of sales orders which reference this part."""
        orders = []

        for line in self.sales_order_line_items.all().prefetch_related('order'):
            if line.order not in orders:
                orders.append(line.order)

        return orders

    def purchase_orders(self):
        """Return a list of purchase orders which reference this part."""
        orders = []

        for part in self.supplier_parts.all().prefetch_related(
            'purchase_order_line_items'
        ):
            for order in part.purchase_orders():
                if order not in orders:
                    orders.append(order)

        return orders

    @property
    @report.mixins.report_attribute()
    def on_order(self) -&gt; Decimal:
        """Return the total number of items on order for this part.

        Note that some supplier parts may have a different pack_quantity attribute,
        and this needs to be taken into account!
        """
        from order.models import PurchaseOrderLineItem

        quantity = 0

        # Find all outstanding PurchaseOrderLineItem objects which reference this part
        lines = PurchaseOrderLineItem.objects.filter(
            order__status__in=PurchaseOrderStatusGroups.OPEN,
            part__part_id=self.pk,
            quantity__gt=F('received'),
        ).prefetch_related('part')

        for line in lines:
            remaining = line.quantity - line.received

            if remaining &gt; 0:
                quantity += line.part.base_quantity(remaining)

        return quantity

    @property
    def has_variants(self):
        """Check if this Part object has variants underneath it."""
        return self.get_all_variants().exists()

    def get_all_variants(self):
        """Return all Part object which exist as a variant under this part."""
        return self.get_descendants(include_self=False)

    @property
    def can_convert(self):
        """Check if this Part can be "converted" to a different variant.

        It can be converted if:
        a) It has non-virtual variant parts underneath it
        b) It has non-virtual template parts above it
        c) It has non-virtual sibling variants
        """
        return self.get_conversion_options().exists()

    def get_conversion_options(self):
        """Return options for converting this part to a "variant" within the same tree.

        a) Variants underneath this one
        b) Immediate parent
        c) Siblings
        """
        parts = []

        # Child parts
        for child in self.get_descendants(include_self=False):
            parts.append(child)

        # Immediate parent, and siblings
        if self.variant_of:
            parts.append(self.variant_of)

            siblings = self.get_siblings(include_self=False)

            for sib in siblings:
                parts.append(sib)

        filtered_parts = Part.objects.filter(pk__in=[part.pk for part in parts])

        # Ensure this part is not in the queryset, somehow
        filtered_parts = filtered_parts.exclude(pk=self.pk)

        filtered_parts = filtered_parts.filter(active=True, virtual=False)

        return filtered_parts

    def get_related_parts(self):
        """Return a set of all related parts for this part."""
        related_parts = set()

        related_parts_1 = self.related_parts_1.filter(part_1__id=self.pk)

        related_parts_2 = self.related_parts_2.filter(part_2__id=self.pk)

        for related_part in related_parts_1:
            # Add to related parts list
            related_parts.add(related_part.part_2)

        for related_part in related_parts_2:
            # Add to related parts list
            related_parts.add(related_part.part_1)

        return related_parts

    @property
    def related_count(self):
        """Return the number of 'related parts' which point to this Part."""
        return len(self.get_related_parts())

    def is_part_low_on_stock(self):
        """Returns True if the total stock for this part is less than the minimum stock level."""
        return self.get_stock_count() &lt; self.minimum_stock


@receiver(post_save, sender=Part, dispatch_uid='part_post_save_log')
def after_save_part(sender, instance: Part, created, **kwargs):
    """Function to be executed after a Part is saved."""
    from django.conf import settings

    from part import tasks as part_tasks

    if instance and not created and not InvenTree.ready.isImportingData():
        # Check part stock only if we are *updating* the part (not creating it)

        # Run this check in the background
        InvenTree.tasks.offload_task(
            part_tasks.notify_low_stock_if_required,
            instance.pk,
            group='notification',
            force_async=not settings.TESTING,  # Force async unless in testing mode
        )

        # Schedule a background task to rebuild any supplier parts
        InvenTree.tasks.offload_task(
            part_tasks.rebuild_supplier_parts,
            instance.pk,
            force_async=True,
            group='part',
        )


class PartPricing(common.models.MetaMixin):
    """Model for caching min/max pricing information for a particular Part.

    It is prohibitively expensive to calculate min/max pricing for a part "on the fly".
    As min/max pricing does not change very often, we pre-calculate and cache these values.

    Whenever pricing is updated, these values are re-calculated and stored.

    Pricing information is cached for:

    - BOM cost (min / max cost of component items)
    - Purchase cost (based on purchase history)
    - Internal cost (based on user-specified InternalPriceBreak data)
    - Supplier price (based on supplier part data)
    - Variant price (min / max cost of any variants)
    - Overall best / worst (based on the values listed above)
    - Sale price break min / max values
    - Historical sale pricing min / max values

    Note that this pricing information does not take "quantity" into account:
    - This provides a simple min / max pricing range, which is quite valuable in a lot of situations
    - Quantity pricing still needs to be calculated
    - Quantity pricing can be viewed from the part detail page
    - Detailed pricing information is very context specific in any case
    """

    # When calculating assembly pricing, we limit the depth of the calculation
    MAX_PRICING_DEPTH = 50

    @property
    def is_valid(self):
        """Return True if the cached pricing is valid."""
        return self.updated is not None

    def convert(self, money):
        """Attempt to convert money value to default currency.

        If a MissingRate error is raised, ignore it and return None
        """
        if money is None:
            return None

        target_currency = currency_code_default()

        try:
            result = convert_money(money, target_currency)
        except MissingRate:
            logger.warning(
                'No currency conversion rate available for %s -&gt; %s',
                money.currency,
                target_currency,
            )
            result = None

        return result

    def schedule_for_update(self, counter: int = 0, refresh: bool = True):
        """Schedule this pricing to be updated.

        Arguments:
            counter: Recursion counter (used to prevent infinite recursion)
            refresh: If specified, the PartPricing object will be refreshed from the database
        """
        import InvenTree.ready

        # If importing data, skip pricing update
        if InvenTree.ready.isImportingData():
            return

        # If running data migrations, skip pricing update
        if InvenTree.ready.isRunningMigrations():
            return

        if (
            not self.part
            or not self.part.pk
            or not Part.objects.filter(pk=self.part.pk).exists()
        ):
            logger.warning(
                'Referenced part instance does not exist - skipping pricing update.'
            )
            return

        try:
            if refresh and self.pk:
                self.refresh_from_db()
        except (PartPricing.DoesNotExist, IntegrityError):
            # Error thrown if this PartPricing instance has already been removed
            logger.warning(
                "Error refreshing PartPricing instance for part '%s'", self.part
            )
            return

        # Ensure that the referenced part still exists in the database
        try:
            p = self.part
            if True:  # refresh and p.pk:
                p.refresh_from_db()
        except IntegrityError:
            logger.exception(
                "Could not update PartPricing as Part '%s' does not exist", self.part
            )
            return

        if self.scheduled_for_update:
            # Ignore if the pricing is already scheduled to be updated
            logger.debug('Pricing for %s already scheduled for update - skipping', p)
            return

        if counter &gt; self.MAX_PRICING_DEPTH:
            # Prevent infinite recursion / stack depth issues
            logger.debug(
                counter, f'Skipping pricing update for {p} - maximum depth exceeded'
            )
            return

        try:
            self.scheduled_for_update = True
            self.save()
        except IntegrityError:
            # An IntegrityError here likely indicates that the referenced part has already been deleted
            logger.exception(
                "Could not save PartPricing for part '%s' to the database", self.part
            )
            return

        import part.tasks as part_tasks

        # Pricing calculations are performed in the background,
        # unless the TESTING_PRICING flag is set
        background = not settings.TESTING or not settings.TESTING_PRICING

        # Offload task to update the pricing
        # Force async, to prevent running in the foreground (unless in testing mode)
        InvenTree.tasks.offload_task(
            part_tasks.update_part_pricing,
            self,
            counter=counter,
            force_async=background,
            group='pricing',
        )

    def update_pricing(
        self,
        counter: int = 0,
        cascade: bool = True,
        previous_min=None,
        previous_max=None,
    ):
        """Recalculate all cost data for the referenced Part instance.

        Arguments:
            counter: Recursion counter (used to prevent infinite recursion)
            cascade: If True, update pricing for all assemblies and templates which use this part
            previous_min: Previous minimum price (used to prevent further updates if unchanged)
            previous_max: Previous maximum price (used to prevent further updates if unchanged)

        """
        # If importing data, skip pricing update
        if InvenTree.ready.isImportingData():
            return

        # If running data migrations, skip pricing update
        if InvenTree.ready.isRunningMigrations():
            return

        if self.pk is not None:
            try:
                self.refresh_from_db()
            except PartPricing.DoesNotExist:
                pass

        self.update_bom_cost(save=False)
        self.update_purchase_cost(save=False)
        self.update_internal_cost(save=False)
        self.update_supplier_cost(save=False)
        self.update_variant_cost(save=False)
        self.update_sale_cost(save=False)

        # Clear scheduling flag
        self.scheduled_for_update = False

        # Note: save method calls update_overall_cost
        try:
            self.save()
        except IntegrityError:
            # Background worker processes may try to concurrently update
            pass

        pricing_changed = False

        # Without previous pricing data, we assume that the pricing has changed
        if previous_min != self.overall_min or previous_max != self.overall_max:
            pricing_changed = True

        # Update parent assemblies and templates
        if pricing_changed and cascade:
            self.update_assemblies(counter)
            self.update_templates(counter)

    def update_assemblies(self, counter: int = 0):
        """Schedule updates for any assemblies which use this part."""
        # If the linked Part is used in any assemblies, schedule a pricing update for those assemblies

        used_in_parts = self.part.get_used_in()

        for p in used_in_parts:
            p.pricing.schedule_for_update(counter=counter + 1)

    def update_templates(self, counter: int = 0):
        """Schedule updates for any template parts above this part."""
        templates = self.part.get_ancestors(include_self=False)

        for p in templates:
            p.pricing.schedule_for_update(counter + 1)

    def save(self, *args, **kwargs):
        """Whenever pricing model is saved, automatically update overall prices."""
        # Update the currency which was used to perform the calculation
        self.currency = currency_code_default()

        try:
            self.update_overall_cost()
            super().save(*args, **kwargs)
        except Exception:
            log_error('PartPricing.save')
            logger.error(
                "Could not save PartPricing for part '%s' to the database", self.part
            )

    def update_bom_cost(self, save=True):
        """Recalculate BOM cost for the referenced Part instance.

        Iterate through the Bill of Materials, and calculate cumulative pricing:

        cumulative_min: The sum of minimum costs for each line in the BOM
        cumulative_max: The sum of maximum costs for each line in the BOM

        Note: The cumulative costs are calculated based on the specified default currency
        """
        if not self.part.assembly:
            # Not an assembly - no BOM pricing
            self.bom_cost_min = None
            self.bom_cost_max = None

            if save:
                self.save()

            # Short circuit - no further operations required
            return

        currency_code = common.currency.currency_code_default()

        cumulative_min = Money(0, currency_code)
        cumulative_max = Money(0, currency_code)

        any_min_elements = False
        any_max_elements = False

        for bom_item in self.part.get_bom_items():
            # Loop through each BOM item which is used to assemble this part

            bom_item_min = None
            bom_item_max = None

            for sub_part in bom_item.get_valid_parts_for_allocation():
                # Check each part which *could* be used

                if sub_part != bom_item.sub_part and not sub_part.active:
                    continue

                sub_part_pricing = sub_part.pricing

                sub_part_min = self.convert(sub_part_pricing.overall_min)
                sub_part_max = self.convert(sub_part_pricing.overall_max)

                if sub_part_min is not None:
                    if bom_item_min is None or sub_part_min &lt; bom_item_min:
                        bom_item_min = sub_part_min

                if sub_part_max is not None:
                    if bom_item_max is None or sub_part_max &gt; bom_item_max:
                        bom_item_max = sub_part_max

            # Update cumulative totals
            if bom_item_min is not None:
                bom_item_min *= bom_item.quantity
                cumulative_min += self.convert(bom_item_min)

                any_min_elements = True

            if bom_item_max is not None:
                bom_item_max *= bom_item.quantity
                cumulative_max += self.convert(bom_item_max)

                any_max_elements = True

        if any_min_elements:
            self.bom_cost_min = cumulative_min
        else:
            self.bom_cost_min = None

        if any_max_elements:
            self.bom_cost_max = cumulative_max
        else:
            self.bom_cost_max = None

        if save:
            self.save()

    def update_purchase_cost(self, save=True):
        """Recalculate historical purchase cost for the referenced Part instance.

        Purchase history only takes into account "completed" purchase orders.
        """
        # Find all line items for completed orders which reference this part
        line_items = OrderModels.PurchaseOrderLineItem.objects.filter(
            order__status=PurchaseOrderStatus.COMPLETE.value,
            received__gt=0,
            part__part=self.part,
        )

        # Exclude line items which do not have an associated price
        line_items = line_items.exclude(purchase_price=None)

        purchase_min = None
        purchase_max = None

        for line in line_items:
            if line.purchase_price is None:
                continue

            # Take supplier part pack size into account
            purchase_cost = self.convert(
                line.purchase_price / line.part.pack_quantity_native
            )

            if purchase_cost is None:
                continue

            if purchase_min is None or purchase_cost &lt; purchase_min:
                purchase_min = purchase_cost

            if purchase_max is None or purchase_cost &gt; purchase_max:
                purchase_max = purchase_cost

        # Also check if manual stock item pricing is included
        if get_global_setting('PRICING_USE_STOCK_PRICING', True):
            items = self.part.stock_items.all()

            # Limit to stock items updated within a certain window
            days = int(get_global_setting('PRICING_STOCK_ITEM_AGE_DAYS', 0))

            if days &gt; 0:
                date_threshold = InvenTree.helpers.current_date() - timedelta(days=days)
                items = items.filter(updated__gte=date_threshold)

            for item in items:
                cost = self.convert(item.purchase_price)

                # Skip if the cost could not be converted (for some reason)
                if cost is None:
                    continue

                if purchase_min is None or cost &lt; purchase_min:
                    purchase_min = cost

                if purchase_max is None or cost &gt; purchase_max:
                    purchase_max = cost

        self.purchase_cost_min = purchase_min
        self.purchase_cost_max = purchase_max

        if save:
            self.save()

    def update_internal_cost(self, save=True):
        """Recalculate internal cost for the referenced Part instance."""
        min_int_cost = None
        max_int_cost = None

        if get_global_setting('PART_INTERNAL_PRICE', False):
            # Only calculate internal pricing if internal pricing is enabled
            for pb in self.part.internalpricebreaks.all():
                cost = self.convert(pb.price)

                if cost is None:
                    # Ignore if cost could not be converted for some reason
                    continue

                if min_int_cost is None or cost &lt; min_int_cost:
                    min_int_cost = cost

                if max_int_cost is None or cost &gt; max_int_cost:
                    max_int_cost = cost

        self.internal_cost_min = min_int_cost
        self.internal_cost_max = max_int_cost

        if save:
            self.save()

    def update_supplier_cost(self, save=True):
        """Recalculate supplier cost for the referenced Part instance.

        - The limits are simply the lower and upper bounds of available SupplierPriceBreaks
        - We do not take "quantity" into account here
        """
        min_sup_cost = None
        max_sup_cost = None

        if self.part.purchaseable:
            # Iterate through each available SupplierPart instance
            for sp in self.part.supplier_parts.all():
                # Iterate through each available SupplierPriceBreak instance
                for pb in sp.pricebreaks.all():
                    if pb.price is None:
                        continue

                    # Ensure we take supplier part pack size into account
                    cost = self.convert(pb.price / sp.pack_quantity_native)

                    if cost is None:
                        continue

                    if min_sup_cost is None or cost &lt; min_sup_cost:
                        min_sup_cost = cost

                    if max_sup_cost is None or cost &gt; max_sup_cost:
                        max_sup_cost = cost

        self.supplier_price_min = min_sup_cost
        self.supplier_price_max = max_sup_cost

        if save:
            self.save()

    def update_variant_cost(self, save=True):
        """Update variant cost values.

        Here we track the min/max costs of any variant parts.
        """
        variant_min = None
        variant_max = None

        active_only = get_global_setting('PRICING_ACTIVE_VARIANTS', False)

        if self.part.is_template:
            variants = self.part.get_descendants(include_self=False)

            for v in variants:
                if active_only and not v.active:
                    # Ignore inactive variant parts
                    continue

                v_min = self.convert(v.pricing.overall_min)
                v_max = self.convert(v.pricing.overall_max)

                if v_min is not None:
                    if variant_min is None or v_min &lt; variant_min:
                        variant_min = v_min

                if v_max is not None:
                    if variant_max is None or v_max &gt; variant_max:
                        variant_max = v_max

        self.variant_cost_min = variant_min
        self.variant_cost_max = variant_max

        if save:
            self.save()

    def update_overall_cost(self):
        """Update overall cost values.

        Here we simply take the minimum / maximum values of the other calculated fields.
        """
        overall_min = None
        overall_max = None

        min_costs = [self.bom_cost_min, self.purchase_cost_min, self.internal_cost_min]

        max_costs = [self.bom_cost_max, self.purchase_cost_max, self.internal_cost_max]

        purchase_history_override = get_global_setting(
            'PRICING_PURCHASE_HISTORY_OVERRIDES_SUPPLIER', False
        )

        if get_global_setting('PRICING_USE_SUPPLIER_PRICING', True):
            # Add supplier pricing data, *unless* historical pricing information should override
            if self.purchase_cost_min is None or not purchase_history_override:
                min_costs.append(self.supplier_price_min)

            if self.purchase_cost_max is None or not purchase_history_override:
                max_costs.append(self.supplier_price_max)

        if get_global_setting('PRICING_USE_VARIANT_PRICING', True):
            # Include variant pricing in overall calculations
            min_costs.append(self.variant_cost_min)
            max_costs.append(self.variant_cost_max)

        # Calculate overall minimum cost
        for cost in min_costs:
            if cost is None:
                continue

            # Ensure we are working in a common currency
            cost = self.convert(cost)

            if overall_min is None or cost &lt; overall_min:
                overall_min = cost

        # Calculate overall maximum cost
        for cost in max_costs:
            if cost is None:
                continue

            # Ensure we are working in a common currency
            cost = self.convert(cost)

            if overall_max is None or cost &gt; overall_max:
                overall_max = cost

        if get_global_setting('PART_BOM_USE_INTERNAL_PRICE', False):
            # Check if internal pricing should override other pricing
            if self.internal_cost_min is not None:
                overall_min = self.internal_cost_min

            if self.internal_cost_max is not None:
                overall_max = self.internal_cost_max

        if self.override_min is not None:
            overall_min = self.convert(self.override_min)

        self.overall_min = overall_min

        if self.override_max is not None:
            overall_max = self.convert(self.override_max)

        self.overall_max = overall_max

    def update_sale_cost(self, save=True):
        """Recalculate sale cost data."""
        # Iterate through the sell price breaks
        min_sell_price = None
        max_sell_price = None

        for pb in self.part.salepricebreaks.all():
            cost = self.convert(pb.price)

            if cost is None:
                continue

            if min_sell_price is None or cost &lt; min_sell_price:
                min_sell_price = cost

            if max_sell_price is None or cost &gt; max_sell_price:
                max_sell_price = cost

        # Record min/max values
        self.sale_price_min = min_sell_price
        self.sale_price_max = max_sell_price

        min_sell_history = None
        max_sell_history = None

        # Calculate sale price history too
        parts = self.part.get_descendants(include_self=True)

        # Find all line items for shipped sales orders which reference this part
        line_items = OrderModels.SalesOrderLineItem.objects.filter(
            order__status__in=SalesOrderStatusGroups.COMPLETE, part__in=parts
        )

        # Exclude line items which do not have associated pricing data
        line_items = line_items.exclude(sale_price=None)

        for line in line_items:
            cost = self.convert(line.sale_price)

            if cost is None:
                continue

            if min_sell_history is None or cost &lt; min_sell_history:
                min_sell_history = cost

            if max_sell_history is None or cost &gt; max_sell_history:
                max_sell_history = cost

        self.sale_history_min = min_sell_history
        self.sale_history_max = max_sell_history

        if save:
            self.save()

    currency = models.CharField(
        default=currency_code_default,
        max_length=10,
        verbose_name=_('Currency'),
        help_text=_('Currency used to cache pricing calculations'),
        validators=[validators.validate_currency_code],
    )

    scheduled_for_update = models.BooleanField(default=False)

    part = models.OneToOneField(
        Part,
        on_delete=models.CASCADE,
        related_name='pricing_data',
        verbose_name=_('Part'),
    )

    bom_cost_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum BOM Cost'),
        help_text=_('Minimum cost of component parts'),
    )

    bom_cost_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum BOM Cost'),
        help_text=_('Maximum cost of component parts'),
    )

    purchase_cost_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Purchase Cost'),
        help_text=_('Minimum historical purchase cost'),
    )

    purchase_cost_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Purchase Cost'),
        help_text=_('Maximum historical purchase cost'),
    )

    internal_cost_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Internal Price'),
        help_text=_('Minimum cost based on internal price breaks'),
    )

    internal_cost_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Internal Price'),
        help_text=_('Maximum cost based on internal price breaks'),
    )

    supplier_price_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Supplier Price'),
        help_text=_('Minimum price of part from external suppliers'),
    )

    supplier_price_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Supplier Price'),
        help_text=_('Maximum price of part from external suppliers'),
    )

    variant_cost_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Variant Cost'),
        help_text=_('Calculated minimum cost of variant parts'),
    )

    variant_cost_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Variant Cost'),
        help_text=_('Calculated maximum cost of variant parts'),
    )

    override_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Cost'),
        help_text=_('Override minimum cost'),
    )

    override_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Cost'),
        help_text=_('Override maximum cost'),
    )

    overall_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Cost'),
        help_text=_('Calculated overall minimum cost'),
    )

    overall_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Cost'),
        help_text=_('Calculated overall maximum cost'),
    )

    sale_price_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Sale Price'),
        help_text=_('Minimum sale price based on price breaks'),
    )

    sale_price_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Sale Price'),
        help_text=_('Maximum sale price based on price breaks'),
    )

    sale_history_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Sale Cost'),
        help_text=_('Minimum historical sale price'),
    )

    sale_history_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Sale Cost'),
        help_text=_('Maximum historical sale price'),
    )


class PartStocktake(models.Model):
    """Model representing a 'stock history' entry for a particular Part.

    A 'stocktake' is a representative count of available stock:
    - Performed on a given date
    - Records quantity of part in stock (across multiple stock items)
    - Records estimated value of "stock on hand"
    """

    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='stocktakes',
        verbose_name=_('Part'),
        help_text=_('Part for stocktake'),
    )

    item_count = models.IntegerField(
        default=1,
        verbose_name=_('Item Count'),
        help_text=_('Number of individual stock entries at time of stocktake'),
    )

    quantity = models.DecimalField(
        max_digits=19,
        decimal_places=5,
        validators=[MinValueValidator(0)],
        verbose_name=_('Quantity'),
        help_text=_('Total available stock at time of stocktake'),
    )

    date = models.DateField(
        verbose_name=_('Date'),
        help_text=_('Date stocktake was performed'),
        auto_now_add=True,
    )

    cost_min = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Minimum Stock Cost'),
        help_text=_('Estimated minimum cost of stock on hand'),
    )

    cost_max = InvenTree.fields.InvenTreeModelMoneyField(
        null=True,
        blank=True,
        verbose_name=_('Maximum Stock Cost'),
        help_text=_('Estimated maximum cost of stock on hand'),
    )


class PartSellPriceBreak(common.models.PriceBreak):
    """Represents a price break for selling this part."""

    class Meta:
        """Metaclass providing extra model definition."""

        verbose_name = _('Part Sale Price Break')
        unique_together = ('part', 'quantity')

    @staticmethod
    def get_api_url():
        """Return the list API endpoint URL associated with the PartSellPriceBreak model."""
        return reverse('api-part-sale-price-list')

    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='salepricebreaks',
        limit_choices_to={'salable': True},
        verbose_name=_('Part'),
    )


class PartInternalPriceBreak(common.models.PriceBreak):
    """Represents a price break for internally selling this part."""

    class Meta:
        """Metaclass providing extra model definition."""

        unique_together = ('part', 'quantity')
        verbose_name = _('Part Internal Price Break')

    @staticmethod
    def get_api_url():
        """Return the list API endpoint URL associated with the PartInternalPriceBreak model."""
        return reverse('api-part-internal-price-list')

    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='internalpricebreaks',
        verbose_name=_('Part'),
    )


class PartStar(models.Model):
    """A PartStar object creates a subscription relationship between a User and a Part.

    It is used to designate a Part as 'subscribed' for a given User.

    Attributes:
        part: Link to a Part object
        user: Link to a User object
    """

    class Meta:
        """Metaclass providing extra model definition."""

        unique_together = ['part', 'user']

    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        verbose_name=_('Part'),
        related_name='starred_users',
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name=_('User'),
        related_name='starred_parts',
    )


class PartCategoryStar(models.Model):
    """A PartCategoryStar creates a subscription relationship between a User and a PartCategory.

    Attributes:
        category: Link to a PartCategory object
        user: Link to a User object
    """

    class Meta:
        """Metaclass providing extra model definition."""

        unique_together = ['category', 'user']

    category = models.ForeignKey(
        PartCategory,
        on_delete=models.CASCADE,
        verbose_name=_('Category'),
        related_name='starred_users',
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name=_('User'),
        related_name='starred_categories',
    )


class PartTestTemplate(InvenTree.models.InvenTreeMetadataModel):
    """A PartTestTemplate defines a 'template' for a test which is required to be run against a StockItem (an instance of the Part).

    The test template applies "recursively" to part variants, allowing tests to be
    defined in a hierarchy.

    Test names are simply strings, rather than enforcing any sort of structure or pattern.
    It is up to the user to determine what tests are defined (and how they are run).

    To enable generation of unique lookup-keys for each test, there are some validation tests
    run on the model (refer to the validate_unique function).
    """

    IMPORT_ID_FIELDS = ['key']

    class Meta:
        """Metaclass options for the PartTestTemplate model."""

        verbose_name = _('Part Test Template')

    def __str__(self):
        """Format a string representation of this PartTestTemplate."""
        return ' | '.join([self.part.name, self.test_name])

    @staticmethod
    def get_api_url():
        """Return the list API endpoint URL associated with the PartTestTemplate model."""
        return reverse('api-part-test-template-list')

    def save(self, *args, **kwargs):
        """Enforce 'clean' operation when saving a PartTestTemplate instance."""
        self.clean()

        super().save(*args, **kwargs)

    def clean(self):
        """Clean fields for the PartTestTemplate model."""
        self.test_name = self.test_name.strip()

        self.key = helpers.generateTestKey(self.test_name)

        if len(self.key) == 0:
            raise ValidationError({
                'test_name': _(
                    'Invalid template name - must include at least one alphanumeric character'
                )
            })

        # Check that 'choices' are in fact valid
        if self.choices is None:
            self.choices = ''
        else:
            self.choices = str(self.choices).strip()

        if self.choices:
            choice_set = set()

            for choice in self.choices.split(','):
                choice = choice.strip()

                # Ignore empty choices
                if not choice:
                    continue

                if choice in choice_set:
                    raise ValidationError({'choices': _('Choices must be unique')})

                choice_set.add(choice)

        self.validate_unique()
        super().clean()

    def validate_unique(self, exclude=None):
        """Test that this test template is 'unique' within this part tree."""
        if not self.part.testable:
            raise ValidationError({
                'part': _('Test templates can only be created for testable parts')
            })

        # Check that this test is unique for this part
        # (including template parts of which this part is a variant)
        parts = self.part.get_ancestors(include_self=True)

        tests = PartTestTemplate.objects.filter(key=self.key, part__in=parts).exclude(
            pk=self.pk
        )

        if tests.exists():
            raise ValidationError({
                'test_name': _(
                    'Test template with the same key already exists for part'
                )
            })

        super().validate_unique(exclude)

    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='test_templates',
        limit_choices_to={'testable': True},
        verbose_name=_('Part'),
    )

    test_name = models.CharField(
        blank=False,
        max_length=100,
        verbose_name=_('Test Name'),
        help_text=_('Enter a name for the test'),
    )

    key = models.CharField(
        blank=True,
        max_length=100,
        verbose_name=_('Test Key'),
        help_text=_('Simplified key for the test'),
    )

    description = models.CharField(
        blank=False,
        null=True,
        max_length=100,
        verbose_name=_('Test Description'),
        help_text=_('Enter description for this test'),
    )

    enabled = models.BooleanField(
        default=True, verbose_name=_('Enabled'), help_text=_('Is this test enabled?')
    )

    required = models.BooleanField(
        default=True,
        verbose_name=_('Required'),
        help_text=_('Is this test required to pass?'),
    )

    requires_value = models.BooleanField(
        default=False,
        verbose_name=_('Requires Value'),
        help_text=_('Does this test require a value when adding a test result?'),
    )

    requires_attachment = models.BooleanField(
        default=False,
        verbose_name=_('Requires Attachment'),
        help_text=_(
            'Does this test require a file attachment when adding a test result?'
        ),
    )

    choices = models.CharField(
        max_length=5000,
        verbose_name=_('Choices'),
        help_text=_('Valid choices for this test (comma-separated)'),
        blank=True,
    )

    def get_choices(self):
        """Return a list of valid choices for this test template."""
        if not self.choices:
            return []

        return [x.strip() for x in self.choices.split(',') if x.strip()]


class BomItem(InvenTree.models.MetadataMixin, InvenTree.models.InvenTreeModel):
    """A BomItem links a part to its component items.

    A part can have a BOM (bill of materials) which defines
    which parts are required (and in what quantity) to make it.

    Attributes:
        part: Link to the parent part (the part that will be produced)
        sub_part: Link to the child part (the part that will be consumed)
        raw_amount: Raw amount of 'sub_part' consumed to produce one 'part' (can be fractional, or use an associated unit)
        quantity: Numerical quantity of 'sub_parts' consumed to produce one 'part'
        optional: Boolean field describing if this BomItem is optional
        consumable: Boolean field describing if this BomItem is considered a 'consumable'
        reference: BOM reference field (e.g. part designators)
        setup_quantity: Extra required quantity for a build, to account for setup losses
        attrition: Estimated losses for a Build, expressed as a percentage (e.g. '2%')
        rounding_multiple: Rounding quantity when calculating the required quantity for a build
        note: Note field for this BOM item
        checksum: Validation checksum for the particular BOM line item
        validated: Boolean field indicating if this BOM item is valid (checksum matches)
        inherited: This BomItem can be inherited by the BOMs of variant parts
        allow_variants: Stock for part variants can be substituted for this BomItem
    """

    class Meta:
        """Metaclass providing extra model definition."""

        verbose_name = _('BOM Item')

    def __str__(self):
        """Return a string representation of this BomItem instance."""
        return f'{decimal2string(self.quantity)} x {self.sub_part.full_name} to make {self.part.full_name}'

    @staticmethod
    def get_api_url():
        """Return the list API endpoint URL associated with the BomItem model."""
        return reverse('api-bom-list')

    def get_assemblies(self):
        """Return a list of assemblies which use this BomItem."""
        assemblies = [self.part]

        if self.inherited:
            assemblies += list(self.part.get_descendants(include_self=False))

        return assemblies

    def get_valid_parts_for_allocation(
        self,
        allow_variants: bool = True,
        allow_substitutes: bool = True,
        allow_inactive: bool = True,
        variant_parts=None,
    ):
        """Return a list of valid parts which can be allocated against this BomItem.

        Arguments:
            allow_variants: If True, include variants of the sub_part
            allow_substitutes: If True, include any directly specified substitute parts
            allow_inactive: If True, include inactive parts in the returned list
            variant_parts: Optional pre-fetched iterable of sub_part's descendants,
                to avoid re-querying the part tree when the caller already has it

        Includes:
        - The referenced sub_part
        - Any directly specified substitute parts
        - If allow_variants is True, all variants of sub_part
        """
        # Set of parts we will allow
        parts = set()

        parts.add(self.sub_part)

        # Variant parts (if allowed)
        if allow_variants and self.allow_variants:
            if variant_parts is None:
                variant_parts = self.sub_part.get_descendants(include_self=False)
            for variant in variant_parts:
                parts.add(variant)

        # Substitute parts
        if allow_substitutes:
            for sub in self.substitutes.all():
                parts.add(sub.part)

                # Account for variants of the substitute part (if allowed)
                if allow_variants and self.allow_variants:
                    for sub_variant in sub.part.get_descendants(include_self=False):
                        parts.add(sub_variant)

        valid_parts = []

        for p in parts:
            # Trackable status must be the same as the sub_part
            if p.trackable != self.sub_part.trackable:
                continue

            # Filter by 'active' status
            if not allow_inactive and not p.active:
                continue

            valid_parts.append(p)

        return valid_parts

    def is_stock_item_valid(self, stock_item):
        """Check if the provided StockItem object is "valid" for assignment against this BomItem."""
        return stock_item.part in self.get_valid_parts_for_allocation()

    def get_stock_filter(self):
        """Return a queryset filter for selecting StockItems which match this BomItem.

        - Allow stock from all directly specified substitute parts
        - If allow_variants is True, allow all part variants
        """
        return Q(part__in=self.get_valid_parts_for_allocation())

    def set_quantity(self, quantity: Decimal | str | float):
        """Update the 'quantity' for this BomItem."""
        self.raw_amount = quantity
        self.recalculate_quantity()

    def recalculate_quantity(self):
        """Recalculate the 'quantity' field based on the 'raw_amount' field."""
        if self.raw_amount is None or self.raw_amount == '':
            self.raw_amount = self.quantity

        # Convert from the "raw amount" to a numerical quantity, using the associated unit (if specified)
        try:
            quantity = InvenTree.conversion.convert_physical_value(
                self.raw_amount, self.sub_part.units, strip_units=False
            )

            if not self.sub_part.units and not InvenTree.conversion.is_dimensionless(
                quantity
            ):
                raise ValidationError({
                    'raw_amount': _('Invalid quantity - no units specified for part')
                })

            allow_zero_qty = get_global_setting('PART_BOM_ALLOW_ZERO_QUANTITY', False)

            if allow_zero_qty:
                if float(quantity.magnitude) &lt; 0:
                    raise ValidationError({
                        'raw_amount': _(
                            'Quantity must be greater than or equal to zero'
                        )
                    })

            else:
                if float(quantity.magnitude) &lt;= 0:
                    raise ValidationError({
                        'raw_amount': _('Quantity must be greater than zero')
                    })

            # Normalize the quantity, to maximum 5 decimal places
            quantity = Decimal(quantity.magnitude)

        except ValidationError as e:
            raise ValidationError({'raw_amount': e.messages})

        # Ensure that the raw_amount is converted to a Decimal value
        # and quantized to a maximum of 5 decimal places (to avoid floating point issues)
        try:
            self.quantity = Decimal(quantity).quantize(
                Decimal('0.00001'), rounding=ROUND_HALF_UP
            )
        except InvalidOperation:
            msg = _('Invalid quantity provided')
            raise ValidationError({'quantity': msg, 'raw_amount': msg})

    def delete(self):
        """Check if this item can be deleted."""
        import part.tasks as part_tasks

        self.check_part_lock(self.part)

        assemblies = self.get_assemblies()
        super().delete()

        for assembly in assemblies:
            # Offload task to update the checksum for this assembly
            InvenTree.tasks.offload_task(
                part_tasks.check_bom_valid, assembly.pk, group='part'
            )

    def save(self, *args, **kwargs):
        """Enforce 'clean' operation when saving a BomItem instance."""
        import part.tasks as part_tasks

        self.clean()

        check_lock = kwargs.pop('check_lock', True)

        if check_lock:
            self.check_part_lock(self.part)

        db_instance = self.get_db_instance()

        # Check if the part was changed
        deltas = self.get_field_deltas()

        if 'part' in deltas and (old_part := deltas['part'].get('old', None)):
            if check_lock:
                self.check_part_lock(old_part)

        # Update the 'validated' field based on checksum calculation
        self.validated = self.is_line_valid

        super().save(*args, **kwargs)

        # Do we need to recalculate the BOM hash for assemblies?
        if not db_instance or any(f in deltas for f in self.hash_fields()):
            # If this is a new BomItem, or if any of the fields used to calculate the hash have changed,
            # then we need to recalculate the BOM checksum for all assemblies which use this BomItem

            assemblies = set()

            if db_instance:
                # Find all assemblies which use this BomItem *before* we save
                assemblies.update(db_instance.get_assemblies())

            # Update the set of assemblies to include those which use this BomItem *after* we save
            assemblies.update(self.get_assemblies())

            for assembly in assemblies:
                # Offload task to update the checksum for this assembly
                InvenTree.tasks.offload_task(
                    part_tasks.check_bom_valid, assembly.pk, group='part'
                )

    def check_part_lock(self, assembly):
        """When editing or deleting a BOM item, check if the assembly is locked.

        If locked, raise an exception.

        Arguments:
            assembly: The assembly part

        Raises:
            ValidationError: If the assembly is locked
        """
        if not get_global_setting('PART_ENABLE_LOCKING'):
            return

        if assembly.locked:
            raise ValidationError(_('BOM item cannot be modified - assembly is locked'))

        # If this BOM item is inherited, check all variants of the assembly
        if self.inherited:
            for part in assembly.get_descendants(include_self=False):
                if part.locked:
                    raise ValidationError(
                        _('BOM item cannot be modified - variant assembly is locked')
                    )

    # A link to the parent part
    # Each part will get a reverse lookup field 'bom_items'
    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='bom_items',
        verbose_name=_('Part'),
        help_text=_('Select parent part'),
        limit_choices_to={'assembly': True},
    )

    # A link to the child item (sub-part)
    # Each part will get a reverse lookup field 'used_in'
    sub_part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='used_in',
        verbose_name=_('Sub part'),
        help_text=_('Select part to be used in BOM'),
        limit_choices_to={'component': True},
    )

    raw_amount = models.CharField(
        max_length=25,
        verbose_name=_('Amount'),
        help_text=_('Amount of sub-part consumed to produce one part'),
        blank=False,
        null=False,
    )

    # Native quantity required
    quantity = models.DecimalField(
        default=1.0,
        max_digits=15,
        decimal_places=5,
        validators=[MinValueValidator(0)],
        verbose_name=_('Quantity'),
        help_text=_('BOM quantity for this BOM item'),
    )

    optional = models.BooleanField(
        default=False,
        verbose_name=_('Optional'),
        help_text=_('This BOM item is optional'),
    )

    consumable = models.BooleanField(
        default=False,
        verbose_name=_('Consumable'),
        help_text=_('This BOM item is consumable (it is not tracked in build orders)'),
    )

    setup_quantity = models.DecimalField(
        default=0,
        max_digits=15,
        decimal_places=5,
        validators=[MinValueValidator(0)],
        verbose_name=_('Setup Quantity'),
        help_text=_('Extra required quantity for a build, to account for setup losses'),
    )

    attrition = models.DecimalField(
        default=0,
        max_digits=6,
        decimal_places=3,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name=_('Attrition'),
        help_text=_(
            'Estimated attrition for a build, expressed as a percentage (0-100)'
        ),
    )

    rounding_multiple = models.DecimalField(
        null=True,
        blank=True,
        default=None,
        max_digits=15,
        decimal_places=5,
        validators=[MinValueValidator(0)],
        verbose_name=_('Rounding Multiple'),
        help_text=_(
            'Round up required production quantity to nearest multiple of this value'
        ),
    )

    reference = models.CharField(
        max_length=5000,
        blank=True,
        verbose_name=_('Reference'),
        help_text=_('BOM item reference'),
    )

    # Note attached to this BOM line item
    note = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('Note'),
        help_text=_('BOM item notes'),
    )

    checksum = models.CharField(
        max_length=128,
        blank=True,
        verbose_name=_('Checksum'),
        help_text=_('BOM line checksum'),
    )

    validated = models.BooleanField(
        default=False,
        verbose_name=_('Validated'),
        help_text=_('This BOM item has been validated'),
    )

    inherited = models.BooleanField(
        default=False,
        verbose_name=_('Gets inherited'),
        help_text=_('This BOM item is inherited by BOMs for variant parts'),
    )

    allow_variants = models.BooleanField(
        default=False,
        verbose_name=_('Allow Variants'),
        help_text=_('Stock items for variant parts can be used for this BOM item'),
    )

    def hash_fields(self) -&gt; list[str]:
        """Return a list of fields to be used for hashing this BOM item.

        These fields are used to calculate the checksum hash of this BOM item.
        """
        return [
            'part',
            'part_id',
            'sub_part',
            'sub_part_id',
            'quantity',
            'setup_quantity',
            'attrition',
            'rounding_multiple',
            'reference',
            'optional',
            'inherited',
            'consumable',
            'allow_variants',
        ]

    def get_item_hash(self) -&gt; str:
        """Calculate the checksum hash of this BOM line item."""
        # Seed the hash with the ID of this BOM item
        result_hash = hashlib.md5(b'')

        for field in self.hash_fields():
            # Get the value of the field
            value = getattr(self, field, None)

            # If the value is None, use an empty string
            if value is None:
                value = ''

            # Normalize decimal values to ensure consistent representation
            # These values are only included if they are non-zero
            # This is to provide some backwards compatibility from before these fields were added
            if value is not None and field in [
                'quantity',
                'attrition',
                'setup_quantity',
                'rounding_multiple',
            ]:
                try:
                    value = normalize(value)

                    if not value or value &lt;= 0:
                        continue
                except Exception:
                    pass

            # Update the hash with the string representation of the value
            result_hash.update(str(value).encode())

        return str(result_hash.digest())

    def validate_hash(self, valid=True):
        """Mark this item as 'valid' (store the checksum hash).

        Args:
            valid: If true, validate the hash, otherwise invalidate it (default = True)
        """
        if valid:
            self.checksum = self.get_item_hash()
        else:
            self.checksum = ''

        # Save the BOM item (bypass lock check)
        self.save(check_lock=False)

    @property
    def is_line_valid(self):
        """Check if this line item has been validated by the user."""
        # Ensure an empty checksum returns False
        if len(self.checksum) == 0:
            return False

        return self.get_item_hash() == self.checksum

    @property
    def is_consumable(self) -&gt; bool:
        """Return True if this BOM line should be treated as consumable.

        This is the case if either:
        - The BOM line itself is marked as consumable
        - The underlying part is marked as consumable
        """
        return self.consumable or self.sub_part.consumable

    @staticmethod
    def consumable_filter(consumable: bool = True, prefix: str = '') -&gt; Q:
        """Return a Q filter which selects BomItem objects based on "effective" consumable status.

        A BomItem is considered "effectively consumable" if either the BOM line itself,
        or the underlying part, is marked as consumable.

        Arguments:
            consumable: If True, return a filter which matches consumable BOM items.
                        If False, return a filter which matches non-consumable BOM items.
            prefix: Optional field lookup prefix, for use against querysets of a
                    related model (e.g. 'bom_item__' when filtering a BuildLine queryset).
        """
        f = Q(**{f'{prefix}consumable': True}) | Q(**{
            f'{prefix}sub_part__consumable': True
        })

        return f if consumable else ~f

    def clean(self):
        """Check validity of the BomItem model.

        Performs model checks beyond simple field validation.

        - A part cannot refer to itself in its BOM
        - A part cannot refer to a part which refers to it

        - If the "sub_part" is trackable, then the "part" must be trackable too!
        """
        super().clean()

        # Recalculate the 'quantity' field based on the 'raw_amount' field
        self.recalculate_quantity()

        try:
            # Check for circular BOM references
            if self.sub_part:
                self.sub_part.check_add_to_bom(self.part, raise_error=True)

                # If the sub_part is 'trackable' then the 'quantity' field must be an integer
                if self.sub_part.trackable:
                    if self.quantity != int(self.quantity):
                        raise ValidationError({
                            'quantity': _(
                                'Quantity must be integer value for trackable parts'
                            )
                        })

                    # Force the upstream part to be trackable if the sub_part is trackable
                    if not self.part.trackable:
                        self.part.trackable = True
                        self.part.clean()
                        self.part.save()
            else:
                raise ValidationError({'sub_part': _('Sub part must be specified')})
        except Part.DoesNotExist:
            raise ValidationError({'sub_part': _('Sub part must be specified')})

    def can_build_quantity(self, available_stock: float) -&gt; int:
        """Calculate the number of assemblies that can be built with the available stock.

        Arguments:
            available_stock: The amount of stock available for this BOM item

        Returns:
            The number of assemblies that can be built with the available stock.
            Returns 0 if the available stock is insufficient.
        """
        # Account for setup quantity
        available_stock = Decimal(max(0, available_stock - self.setup_quantity))
        quantity_decimal = Decimal(self.quantity)
        attrition_decimal = Decimal(self.attrition) / 100
        n = quantity_decimal * (1 + attrition_decimal)

        if n &lt;= 0:
            return 0.0

        return int(Decimal(available_stock) / n)

    def get_required_quantity(self, build_quantity: float) -&gt; float:
        """Calculate the required part quantity, based on the supplied build_quantity.

        Arguments:
            build_quantity: Number of assemblies to build

        Returns:
            Production quantity required for this component
        """
        # Base quantity requirement
        required = self.quantity * build_quantity

        # Account for attrition
        if self.attrition &gt; 0:
            try:
                # Convert attrition percentage to decimal
                attrition = Decimal(self.attrition) / Decimal(100)
                required *= 1 + attrition
            except Exception:
                log_error('bom_item.get_required_quantity')

        # Account for setup quantity
        if self.setup_quantity &gt; 0:
            try:
                setup_quantity = Decimal(self.setup_quantity)
                required += setup_quantity
            except Exception:
                log_error('bom_item.get_required_quantity')

        # We now have the total requirement
        # If a "rounding_multiple" is specified, then round up to the nearest multiple
        if self.rounding_multiple and self.rounding_multiple &gt; 0:
            try:
                round_up = Decimal(self.rounding_multiple)
                value = Decimal(required)
                value = math.ceil(value / round_up) * round_up
                required = float(value)
            except InvalidOperation:
                log_error('bom_item.get_required_quantity')

        return required


@receiver(post_save, sender=BomItem, dispatch_uid='update_bom_build_lines')
def update_bom_build_lines(sender, instance, created, **kwargs):
    """Update existing build orders when a BomItem is created or edited."""
    if InvenTree.ready.canAppAccessDatabase() and not InvenTree.ready.isImportingData():
        import build.tasks

        InvenTree.tasks.offload_task(
            build.tasks.update_build_order_lines, instance.pk, group='build'
        )


@receiver(post_save, sender=BomItem, dispatch_uid='post_save_bom_item')
@receiver(
    post_save, sender=PartSellPriceBreak, dispatch_uid='post_save_sale_price_break'
)
@receiver(
    post_save,
    sender=PartInternalPriceBreak,
    dispatch_uid='post_save_internal_price_break',
)
def update_pricing_after_edit(sender, instance, created, **kwargs):
    """Callback function when a part price break is created or updated."""
    # Update part pricing *unless* we are importing data
    if (
        InvenTree.ready.canAppAccessDatabase(allow_test=settings.TESTING_PRICING)
        and not InvenTree.ready.isImportingData()
    ):
        if instance.part:
            instance.part.schedule_pricing_update(create=True)


@receiver(post_delete, sender=BomItem, dispatch_uid='post_delete_bom_item')
@receiver(
    post_delete, sender=PartSellPriceBreak, dispatch_uid='post_delete_sale_price_break'
)
@receiver(
    post_delete,
    sender=PartInternalPriceBreak,
    dispatch_uid='post_delete_internal_price_break',
)
def update_pricing_after_delete(sender, instance, **kwargs):
    """Callback function when a part price break is deleted."""
    # Update part pricing *unless* we are importing data
    if (
        InvenTree.ready.canAppAccessDatabase(allow_test=settings.TESTING_PRICING)
        and not InvenTree.ready.isImportingData()
    ):
        if instance.part:
            instance.part.schedule_pricing_update(create=False)


class BomItemSubstitute(InvenTree.models.InvenTreeMetadataModel):
    """A BomItemSubstitute provides a specification for alternative parts, which can be used in a bill of materials.

    Attributes:
        bom_item: Link to the parent BomItem instance
        part: The part which can be used as a substitute
    """

    class Meta:
        """Metaclass providing extra model definition."""

        verbose_name = _('BOM Item Substitute')

        # Prevent duplication of substitute parts
        unique_together = ('part', 'bom_item')

    def save(self, *args, **kwargs):
        """Enforce a full_clean when saving the BomItemSubstitute model."""
        self.full_clean()

        super().save(*args, **kwargs)

    def validate_unique(self, exclude=None):
        """Ensure that this BomItemSubstitute is "unique".

        Ensure:
        - It cannot point to the same "part" as the "sub_part" of the parent "bom_item"
        """
        super().validate_unique(exclude=exclude)

        if self.part == self.bom_item.sub_part:
            raise ValidationError({
                'part': _('Substitute part cannot be the same as the master part')
            })

    @staticmethod
    def get_api_url():
        """Returns the list API endpoint URL associated with this model."""
        return reverse('api-bom-substitute-list')

    bom_item = models.ForeignKey(
        BomItem,
        on_delete=models.CASCADE,
        related_name='substitutes',
        verbose_name=_('BOM Item'),
        help_text=_('Parent BOM item'),
    )

    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='substitute_items',
        verbose_name=_('Part'),
        help_text=_('Substitute part'),
        limit_choices_to={'component': True},
    )


class PartRelated(InvenTree.models.InvenTreeMetadataModel):
    """Store and handle related parts (eg. mating connector, crimps, etc.)."""

    class Meta:
        """Metaclass defines extra model properties."""

        unique_together = ('part_1', 'part_2')

    part_1 = models.ForeignKey(
        Part,
        related_name='related_parts_1',
        verbose_name=_('Part 1'),
        on_delete=models.CASCADE,
    )

    part_2 = models.ForeignKey(
        Part,
        related_name='related_parts_2',
        on_delete=models.CASCADE,
        verbose_name=_('Part 2'),
        help_text=_('Select Related Part'),
    )

    note = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('Note'),
        help_text=_('Note for this relationship'),
    )

    def __str__(self):
        """Return a string representation of this Part-Part relationship."""
        return f'{self.part_1} &lt;--&gt; {self.part_2}'

    def save(self, *args, **kwargs):
        """Enforce a 'clean' operation when saving a PartRelated instance."""
        self.clean()
        self.validate_unique()
        super().save(*args, **kwargs)

    def clean(self):
        """Overwrite clean method to check that relation is unique."""
        super().clean()

        if self.part_1 == self.part_2:
            raise ValidationError(
                _('Part relationship cannot be created between a part and itself')
            )

        # Check for inverse relationship
        if PartRelated.objects.filter(part_1=self.part_2, part_2=self.part_1).exists():
            raise ValidationError(_('Duplicate relationship already exists'))

  </file>
  <file path="src/backend/InvenTree/part/serializers.py">
"""DRF data serializers for Part app."""

import os
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import ExpressionWrapper, F, Q
from django.db.models.functions import Greatest
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

import structlog
from djmoney.contrib.exchange.exceptions import MissingRate
from djmoney.contrib.exchange.models import convert_money
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from sql_util.utils import SubqueryCount

import common.currency
import common.filters
import common.serializers
import company.models
import InvenTree.conversion
import InvenTree.helpers
import InvenTree.serializers
import part.filters as part_filters
import part.helpers as part_helpers
import stock.models
import users.models
from data_exporter.mixins import DataExportSerializerMixin
from importer.registry import register_importer
from InvenTree.mixins import DataImportExportSerializerMixin
from InvenTree.ready import isGeneratingSchema
from InvenTree.serializers import OptionalField, TreePathSerializer
from users.serializers import UserSerializer

from .models import (
    BomItem,
    BomItemSubstitute,
    Part,
    PartCategory,
    PartCategoryParameterTemplate,
    PartInternalPriceBreak,
    PartPricing,
    PartRelated,
    PartSellPriceBreak,
    PartStar,
    PartStocktake,
    PartTestTemplate,
)

logger = structlog.get_logger('inventree')


class CategoryDeleteSerializer(serializers.Serializer):
    """Serializer for deleting a PartCategory instance."""

    class Meta:
        """Metaclass options."""

        fields = ['delete_child_categories', 'delete_parts']

    delete_child_categories = serializers.BooleanField(
        label=_('Delete Subcategories'),
        help_text=_('Delete all sub-categories contained within this category'),
        required=True,
    )

    delete_parts = serializers.BooleanField(
        label=_('Delete Parts'),
        help_text=_('Delete all parts contained within this category'),
        required=True,
    )


@register_importer()
class CategorySerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    DataImportExportSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for PartCategory."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartCategory
        fields = [
            'pk',
            'name',
            'description',
            'default_location',
            'default_keywords',
            'level',
            'parent',
            'part_count',
            'subcategories',
            'pathstring',
            'path',
            'starred',
            'structural',
            'icon',
            'parent_default_location',
            # Optional fields
            'parameters',
        ]
        read_only_fields = ['level', 'pathstring']

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate extra information to the queryset."""
        # Annotate the number of 'parts' which exist in each category (including subcategories!)
        queryset = queryset.annotate(
            part_count=part_filters.annotate_category_parts(),
            subcategories=part_filters.annotate_sub_categories(),
        )

        queryset = queryset.annotate(
            parent_default_location=part_filters.annotate_default_location('parent__')
        )

        return queryset

    parent = serializers.PrimaryKeyRelatedField(
        queryset=PartCategory.objects.all(),
        required=False,
        allow_null=True,
        label=_('Parent Category'),
        help_text=_('Parent part category'),
    )

    part_count = serializers.IntegerField(
        read_only=True, allow_null=True, label=_('Parts')
    )

    subcategories = serializers.IntegerField(
        read_only=True, allow_null=True, label=_('Subcategories')
    )

    level = serializers.IntegerField(read_only=True)

    starred = serializers.SerializerMethodField()

    def get_starred(self, category) -&gt; bool:
        """Return True if the category is directly "starred" by the current user."""
        if not self.request or not self.request.user:
            return False

        # Cache the "starred_categories" list for the current user
        if not hasattr(self, 'starred_categories'):
            self.starred_categories = [
                star.category.pk for star in self.request.user.starred_categories.all()
            ]

        return category.pk in self.starred_categories

    path = OptionalField(
        serializer_class=TreePathSerializer,
        serializer_kwargs={
            'source': 'get_path',
            'many': True,
            'read_only': True,
            'allow_null': True,
        },
        default_include=False,
        filter_name='path_detail',
    )

    icon = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text=_('Icon (optional)'),
        max_length=100,
    )

    parent_default_location = serializers.IntegerField(read_only=True, allow_null=True)

    parameters = common.filters.enable_parameters_filter()


class CategoryTreeSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for PartCategory tree."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartCategory
        fields = [
            'pk',
            'name',
            'description',
            'pathstring',
            'parent',
            'tree_id',
            'level',
            'icon',
            'structural',
            'subcategories',
        ]

    subcategories = serializers.IntegerField(label=_('Subcategories'), read_only=True)

    icon = serializers.CharField(
        required=False, allow_blank=True, help_text=_('Icon (optional)'), max_length=100
    )

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the queryset with the number of subcategories."""
        return queryset.annotate(subcategories=part_filters.annotate_sub_categories())


@register_importer()
class PartTestTemplateSerializer(
    DataImportExportSerializerMixin, InvenTree.serializers.InvenTreeModelSerializer
):
    """Serializer for the PartTestTemplate class."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartTestTemplate

        fields = [
            'pk',
            'key',
            'part',
            'test_name',
            'description',
            'enabled',
            'required',
            'requires_value',
            'requires_attachment',
            'results',
            'choices',
        ]

    key = serializers.CharField(read_only=True)
    results = serializers.IntegerField(
        label=_('Results'),
        help_text=_('Number of results recorded against this template'),
        read_only=True,
    )

    @staticmethod
    def annotate_queryset(queryset):
        """Custom query annotations for the PartTestTemplate serializer."""
        return queryset.annotate(results=SubqueryCount('test_results'))


@register_importer()
class PartSalePriceSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    DataImportExportSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for sale prices for Part model."""

    no_filters = True

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartSellPriceBreak
        fields = ['pk', 'part', 'quantity', 'price', 'price_currency']

    quantity = InvenTree.serializers.InvenTreeDecimalField()

    price = InvenTree.serializers.InvenTreeMoneySerializer(allow_null=True)

    price_currency = InvenTree.serializers.InvenTreeCurrencySerializer(
        help_text=_('Purchase currency of this stock item')
    )


@register_importer()
class PartInternalPriceSerializer(
    DataImportExportSerializerMixin, InvenTree.serializers.InvenTreeModelSerializer
):
    """Serializer for internal prices for Part model."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartInternalPriceBreak
        fields = ['pk', 'part', 'quantity', 'price', 'price_currency']

    quantity = InvenTree.serializers.InvenTreeDecimalField()

    price = InvenTree.serializers.InvenTreeMoneySerializer(allow_null=True)

    price_currency = InvenTree.serializers.InvenTreeCurrencySerializer(
        help_text=_('Purchase currency of this stock item')
    )


class PartThumbSerializer(serializers.Serializer):
    """Serializer for the 'image' field of the Part model.

    Used to serve and display existing Part images.
    """

    image = InvenTree.serializers.InvenTreeImageSerializerField(read_only=True)
    count = serializers.IntegerField(read_only=True)


class PartThumbSerializerUpdate(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for updating Part thumbnail."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = Part
        fields = ['image']

    def validate_image(self, value):
        """Check that file is an image."""
        validate = InvenTree.helpers.TestIfImage(value)
        if not validate:
            raise serializers.ValidationError(_('File is not an image'))
        return value

    image = InvenTree.serializers.InvenTreeAttachmentSerializerField(required=True)


class PartBriefSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for Part (brief detail)."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = Part
        fields = [
            'pk',
            'IPN',
            'barcode_hash',
            'category_default_location',
            'default_location',
            'default_expiry',
            'name',
            'revision',
            'full_name',
            'description',
            'image',
            'thumbnail',
            'active',
            'locked',
            'assembly',
            'component',
            'minimum_stock',
            'is_template',
            'purchaseable',
            'salable',
            'testable',
            'trackable',
            'virtual',
            'consumable',
            'units',
            'pricing_min',
            'pricing_max',
        ]
        read_only_fields = ['barcode_hash']

    category_default_location = serializers.IntegerField(
        read_only=True, allow_null=True
    )

    image = InvenTree.serializers.InvenTreeImageSerializerField(
        read_only=True, allow_null=True
    )
    thumbnail = serializers.CharField(source='get_thumbnail_url', read_only=True)

    IPN = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=_('Internal Part Number'),
        max_length=100,
    )

    revision = serializers.CharField(
        required=False, default='', allow_blank=True, allow_null=True, max_length=100
    )

    # Pricing fields
    pricing_min = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={
            'source': 'pricing_data.overall_min',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    pricing_max = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={
            'source': 'pricing_data.overall_max',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )


class InitialStockSerializer(serializers.Serializer):
    """Serializer for creating initial stock quantity."""

    class Meta:
        """Metaclass options."""

        fields = ['quantity', 'location']

    quantity = serializers.DecimalField(
        max_digits=15,
        decimal_places=5,
        validators=[MinValueValidator(0)],
        label=_('Initial Stock Quantity'),
        help_text=_(
            'Specify initial stock quantity for this Part. If quantity is zero, no stock is added.'
        ),
        required=True,
    )

    location = serializers.PrimaryKeyRelatedField(
        queryset=stock.models.StockLocation.objects.all(),
        label=_('Initial Stock Location'),
        help_text=_('Specify initial stock location for this Part'),
        allow_null=True,
        required=False,
    )


class InitialSupplierSerializer(serializers.Serializer):
    """Serializer for adding initial supplier / manufacturer information."""

    class Meta:
        """Metaclass options."""

        fields = ['supplier', 'sku', 'manufacturer', 'mpn']

    supplier = serializers.PrimaryKeyRelatedField(
        queryset=company.models.Company.objects.all(),
        label=_('Supplier'),
        help_text=_('Select supplier (or leave blank to skip)'),
        allow_null=True,
        required=False,
    )

    sku = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        label=_('SKU'),
        help_text=_('Supplier stock keeping unit'),
    )

    manufacturer = serializers.PrimaryKeyRelatedField(
        queryset=company.models.Company.objects.all(),
        label=_('Manufacturer'),
        help_text=_('Select manufacturer (or leave blank to skip)'),
        allow_null=True,
        required=False,
    )

    mpn = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        label=_('MPN'),
        help_text=_('Manufacturer part number'),
    )

    def validate_supplier(self, company):
        """Validation for the provided Supplier."""
        if company and not company.is_supplier:
            raise serializers.ValidationError(
                _('Selected company is not a valid supplier')
            )

        return company

    def validate_manufacturer(self, company):
        """Validation for the provided Manufacturer."""
        if company and not company.is_manufacturer:
            raise serializers.ValidationError(
                _('Selected company is not a valid manufacturer')
            )

        return company

    def validate(self, data):
        """Extra validation for this serializer."""
        if company.models.ManufacturerPart.objects.filter(
            manufacturer=data.get('manufacturer', None), MPN=data.get('mpn', '')
        ).exists():
            raise serializers.ValidationError({
                'mpn': _('Manufacturer part matching this MPN already exists')
            })

        if company.models.SupplierPart.objects.filter(
            supplier=data.get('supplier', None), SKU=data.get('sku', '')
        ).exists():
            raise serializers.ValidationError({
                'sku': _('Supplier part matching this SKU already exists')
            })

        return data


class DefaultLocationSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Brief serializer for a StockLocation object.

    Defined here, rather than stock.serializers, to negotiate circular imports.
    """

    class Meta:
        """Metaclass options."""

        import stock.models as stock_models

        model = stock_models.StockLocation
        fields = ['pk', 'name', 'pathstring']


@register_importer()
class PartSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    DataImportExportSerializerMixin,
    InvenTree.serializers.NotesFieldMixin,
    InvenTree.serializers.InvenTreeTaggitSerializer,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for complete detail information of a part.

    Used when displaying all details of a single component.
    """

    import_exclude_fields = ['creation_date', 'creation_user']

    class Meta:
        """Metaclass defining serializer fields."""

        model = Part
        partial = True
        fields = [
            'active',
            'assembly',
            'barcode_hash',
            'category',
            'category_detail',
            'category_path',
            'category_name',
            'component',
            'creation_date',
            'creation_user',
            'default_expiry',
            'default_location',
            'default_location_detail',
            'description',
            'full_name',
            'image',
            'existing_image',
            'IPN',
            'is_template',
            'keywords',
            'link',
            'locked',
            'minimum_stock',
            'maximum_stock',
            'name',
            'notes',
            'parameters',
            'pk',
            'purchaseable',
            'revision',
            'revision_of',
            'revision_count',
            'salable',
            'starred',
            'thumbnail',
            'testable',
            'trackable',
            'units',
            'variant_of',
            'virtual',
            'consumable',
            'pricing_min',
            'pricing_max',
            'pricing_updated',
            'responsible',
            'price_breaks',
            # Annotated fields
            'allocated_to_build_orders',
            'allocated_to_sales_orders',
            'building',
            'scheduled_to_build',
            'category_default_location',
            'in_stock',
            'ordering',
            'required_for_build_orders',
            'required_for_sales_orders',
            'stock_item_count',
            'total_in_stock',
            'external_stock',
            'unallocated_stock',
            'variant_stock',
            # Fields only used for Part creation
            'duplicate',
            'initial_stock',
            'initial_supplier',
            'copy_category_parameters',
            'tags',
        ]
        read_only_fields = ['barcode_hash', 'creation_date', 'creation_user']

    def __init__(self, *args, **kwargs):
        """Custom initialization method for PartSerializer.

        - Allows us to optionally pass extra fields based on the query.
        """
        create = kwargs.pop('create', False)

        super().__init__(*args, **kwargs)

        if isGeneratingSchema():
            return

        if not create:
            # These fields are only used for the LIST API endpoint
            for f in self.skip_create_fields():
                # Fields required for certain operations, but are not part of the model
                if f in ['existing_image']:
                    continue
                self.fields.pop(f, None)

    def get_api_url(self):
        """Return the API url associated with this serializer."""
        return reverse_lazy('api-part-list')

    def skip_create_fields(self):
        """Skip these fields when instantiating a new Part instance."""
        fields = super().skip_create_fields()

        fields += [
            'duplicate',
            'initial_stock',
            'initial_supplier',
            'copy_category_parameters',
            'existing_image',
        ]

        return fields

    @staticmethod
    def annotate_queryset(queryset):
        """Add some extra annotations to the queryset.

        Performing database queries as efficiently as possible, to reduce database trips.
        """
        # Annotate with the total number of revisions
        queryset = queryset.annotate(revision_count=SubqueryCount('revisions'))

        # Annotate with the total number of stock items
        queryset = queryset.annotate(stock_item_count=SubqueryCount('stock_items'))

        # Annotate with the total variant stock quantity
        variant_query = part_filters.variant_stock_query()

        queryset = queryset.annotate(
            variant_stock=part_filters.annotate_variant_quantity(
                variant_query, reference='quantity'
            )
        )

        # Annotate with the total 'building' quantity
        queryset = queryset.annotate(
            building=part_filters.annotate_in_production_quantity()
        )

        queryset = queryset.annotate(
            scheduled_to_build=part_filters.annotate_scheduled_to_build_quantity()
        )

        queryset = queryset.annotate(
            ordering=part_filters.annotate_on_order_quantity(),
            in_stock=part_filters.annotate_total_stock(),
            allocated_to_sales_orders=part_filters.annotate_sales_order_allocations(),
            # NOTE: for now, decided that allocations to Transfer Orders don't reduce available stock
            # allocated_to_transfer_orders=part_filters.annotate_transfer_order_allocations(),
            allocated_to_build_orders=part_filters.annotate_build_order_allocations(),
        )

        # Annotate the queryset with the 'total_in_stock' quantity
        # This is the 'in_stock' quantity summed with the 'variant_stock' quantity
        queryset = queryset.annotate(
            total_in_stock=ExpressionWrapper(
                F('in_stock') + F('variant_stock'), output_field=models.DecimalField()
            )
        )

        queryset = queryset.annotate(
            external_stock=part_filters.annotate_total_stock(
                filter=Q(location__external=True)
            )
        )

        # Annotate with the total 'available stock' quantity
        # This is the current stock, minus any allocations
        queryset = queryset.annotate(
            unallocated_stock=Greatest(
                ExpressionWrapper(
                    F('total_in_stock')
                    - F('allocated_to_sales_orders')
                    # NOTE: for now, decided that allocations to Transfer Orders don't reduce available stock
                    # - F('allocated_to_transfer_orders'),
                    - F('allocated_to_build_orders'),
                    output_field=models.DecimalField(),
                ),
                Decimal(0),
                output_field=models.DecimalField(),
            )
        )

        # Annotate with the total 'required for builds' quantity
        # NOTE: for now, we don't consider transfer orders for required quantities
        #       and they are assumed to operate on stock that already exists.
        queryset = queryset.annotate(
            required_for_build_orders=part_filters.annotate_build_order_requirements(),
            required_for_sales_orders=part_filters.annotate_sales_order_requirements(),
        )

        queryset = queryset.annotate(
            category_default_location=part_filters.annotate_default_location(
                'category__'
            )
        )

        return queryset

    def get_starred(self, part) -&gt; bool:
        """Return "true" if the part is starred by the current user."""
        if not self.request or not self.request.user:
            return False

        # Cache the "starred_parts" list for the current user
        if not hasattr(self, 'starred_parts'):
            self.starred_parts = [
                star.part.pk for star in self.request.user.starred_parts.all()
            ]

        return part.pk in self.starred_parts

    # Extra detail for the category
    category_detail = OptionalField(
        serializer_class=CategorySerializer,
        serializer_kwargs={
            'source': 'category',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        prefetch_fields=['category'],
    )

    category_path = OptionalField(
        serializer_class=TreePathSerializer,
        serializer_kwargs={
            'source': 'category.get_path',
            'many': True,
            'read_only': True,
            'allow_null': True,
        },
        filter_name='path_detail',
        prefetch_fields=['category'],
    )

    default_location_detail = OptionalField(
        serializer_class=DefaultLocationSerializer,
        serializer_kwargs={
            'source': 'default_location',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        filter_name='location_detail',
        prefetch_fields=['default_location'],
    )

    category_name = serializers.CharField(
        source='category.name', read_only=True, label=_('Category Name')
    )

    responsible = serializers.PrimaryKeyRelatedField(
        queryset=users.models.Owner.objects.all(),
        required=False,
        allow_null=True,
        source='responsible_owner',
    )

    creation_user = serializers.PrimaryKeyRelatedField(
        queryset=users.models.User.objects.all(), required=False, allow_null=True
    )

    IPN = serializers.CharField(
        required=False, default='', allow_blank=True, label=_('IPN'), max_length=100
    )

    revision = serializers.CharField(
        required=False, default='', allow_blank=True, allow_null=True, max_length=100
    )

    # Annotated fields
    allocated_to_build_orders = serializers.FloatField(read_only=True, allow_null=True)
    allocated_to_sales_orders = serializers.FloatField(read_only=True, allow_null=True)

    building = serializers.FloatField(
        read_only=True,
        allow_null=True,
        label=_('Building'),
        help_text=_('Quantity of this part currently being in production'),
    )

    scheduled_to_build = serializers.FloatField(
        read_only=True,
        allow_null=True,
        label=_('Scheduled to Build'),
        help_text=_('Outstanding quantity of this part scheduled to be built'),
    )

    in_stock = serializers.FloatField(
        read_only=True, allow_null=True, label=_('In Stock')
    )

    ordering = serializers.FloatField(
        read_only=True, allow_null=True, label=_('On Order')
    )

    required_for_build_orders = serializers.IntegerField(
        read_only=True, allow_null=True
    )

    required_for_sales_orders = serializers.IntegerField(
        read_only=True, allow_null=True
    )

    stock_item_count = serializers.IntegerField(
        read_only=True, allow_null=True, label=_('Stock Items')
    )

    revision_count = serializers.IntegerField(
        read_only=True, allow_null=True, label=_('Revisions')
    )

    total_in_stock = serializers.FloatField(
        read_only=True, allow_null=True, label=_('Total Stock')
    )

    external_stock = serializers.FloatField(
        read_only=True, allow_null=True, label=_('External Stock')
    )

    unallocated_stock = serializers.FloatField(
        read_only=True, allow_null=True, label=_('Unallocated Stock')
    )

    category_default_location = serializers.IntegerField(
        read_only=True, allow_null=True
    )

    variant_stock = serializers.FloatField(
        read_only=True, allow_null=True, label=_('Variant Stock')
    )

    minimum_stock = serializers.FloatField(
        required=False, label=_('Minimum Stock'), default=0
    )

    maximum_stock = serializers.FloatField(
        required=False, label=_('Maximum Stock'), default=0
    )

    image = InvenTree.serializers.InvenTreeImageSerializerField(
        required=False, allow_null=True
    )
    thumbnail = serializers.CharField(source='get_thumbnail_url', read_only=True)
    starred = serializers.SerializerMethodField()

    # PrimaryKeyRelated fields (Note: enforcing field type here results in much faster queries, somehow...)
    category = serializers.PrimaryKeyRelatedField(
        queryset=PartCategory.objects.all(), required=False, allow_null=True
    )

    # Pricing fields
    pricing_min = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={
            'source': 'pricing_data.overall_min',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    pricing_max = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={
            'source': 'pricing_data.overall_max',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    pricing_updated = OptionalField(
        serializer_class=serializers.DateTimeField,
        serializer_kwargs={
            'source': 'pricing_data.updated',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    parameters = common.filters.enable_parameters_filter()

    tags = common.filters.enable_tags_filter()

    price_breaks = OptionalField(
        serializer_class=PartSalePriceSerializer,
        serializer_kwargs={
            'source': 'salepricebreaks',
            'many': True,
            'read_only': True,
            'allow_null': True,
        },
        default_include=False,
        filter_name='price_breaks',
        prefetch_fields=['salepricebreaks'],
    )

    # Extra fields used only for creation of a new Part instance
    duplicate = InvenTree.serializers.DuplicateOptionsSerializer(
        Part.objects.all(),
        label=_('Duplicate Part'),
        help_text=_('Copy initial data from another Part'),
        copy_parameters=True,
        copy_fields=[
            {
                'name': 'copy_image',
                'label': _('Copy Image'),
                'help_text': _('Copy image from original part'),
                'default': False,
            },
            {
                'name': 'copy_bom',
                'label': _('Copy BOM'),
                'help_text': _('Copy bill of materials from original part'),
                'default': False,
            },
            {
                'name': 'copy_notes',
                'label': _('Copy Notes'),
                'help_text': _('Copy notes from original part'),
                'default': True,
            },
            {
                'name': 'copy_tests',
                'label': _('Copy Tests'),
                'help_text': _('Copy test templates from original part'),
                'default': False,
            },
        ],
    )

    initial_stock = InitialStockSerializer(
        label=_('Initial Stock'),
        help_text=_('Create Part with initial stock quantity'),
        write_only=True,
        required=False,
    )

    initial_supplier = InitialSupplierSerializer(
        label=_('Supplier Information'),
        help_text=_('Add initial supplier information for this part'),
        write_only=True,
        required=False,
    )

    copy_category_parameters = serializers.BooleanField(
        default=True,
        required=False,
        write_only=True,
        label=_('Copy Category Parameters'),
        help_text=_('Copy parameter templates from selected part category'),
    )

    # Allow selection of an existing part image file
    existing_image = serializers.CharField(
        label=_('Existing Image'),
        help_text=_('Filename of an existing part image'),
        write_only=True,
        required=False,
        allow_blank=False,
    )

    def validate_existing_image(self, img):
        """Validate the selected image file."""
        if not img:
            return img

        img = img.split(os.path.sep)[-1]

        # Ensure that the file actually exists
        img_path = os.path.join(part_helpers.get_part_image_directory(), img)

        if not os.path.exists(img_path) or not os.path.isfile(img_path):
            raise ValidationError(_('Image file does not exist'))

        return img

    @transaction.atomic
    def create(self, validated_data):
        """Custom method for creating a new Part instance using this serializer."""
        duplicate = validated_data.pop('duplicate', None)
        initial_stock = validated_data.pop('initial_stock', None)
        initial_supplier = validated_data.pop('initial_supplier', None)
        copy_category_parameters = validated_data.pop('copy_category_parameters', False)

        # Additional data to apply to the serializer
        extra_data = {}

        if request := self.context.get('request'):
            extra_data['creation_user'] = request.user

        instance = super().create({**validated_data, **extra_data})

        # Copy data from original Part
        if duplicate:
            original = duplicate['original']

            if duplicate.get('copy_bom', False):
                instance.copy_bom_from(original)

            if duplicate.get('copy_notes', False):
                instance.notes = original.notes
                instance.save()

            if duplicate.get('copy_image', False):
                instance.image = original.image
                instance.save()

            if duplicate.get('copy_parameters', False):
                instance.copy_parameters_from(original)

            if duplicate.get('copy_tests', False):
                instance.copy_tests_from(original)

        # Duplicate parameter data from part category (and parents)
        if copy_category_parameters and instance.category is not None:
            # Get flattened list of parent categories
            instance.copy_category_parameters(instance.category)

        # Create initial stock entry
        if initial_stock:
            quantity = initial_stock['quantity']
            location = initial_stock.get('location', None) or instance.default_location

            if quantity &gt; 0:
                stockitem = stock.models.StockItem(
                    part=instance, quantity=quantity, location=location
                )

                if request := self.context.get('request', None):
                    stockitem.save(user=request.user)

        # Create initial supplier information
        if initial_supplier:
            manufacturer = initial_supplier.get('manufacturer', None)
            mpn = initial_supplier.get('mpn', '')

            if manufacturer and mpn:
                manufacturer_part = company.models.ManufacturerPart.objects.create(
                    part=instance, manufacturer=manufacturer, MPN=mpn
                )
            else:
                manufacturer_part = None

            supplier = initial_supplier.get('supplier', None)
            sku = initial_supplier.get('sku', '')

            if supplier and sku:
                company.models.SupplierPart.objects.create(
                    part=instance,
                    supplier=supplier,
                    SKU=sku,
                    manufacturer_part=manufacturer_part,
                )

        return instance

    def save(self):
        """Save the Part instance."""
        super().save()

        part = self.instance
        data = self.validated_data

        # TODO: Remove the existing_image field entirely!
        existing_image = data.pop('existing_image', None)

        if existing_image:
            img_path = os.path.join(part_helpers.PART_IMAGE_DIR, existing_image)

            part.image = img_path
            part.save()

        return self.instance


class PartBomValidateSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for Part BOM information."""

    class Meta:
        """Metaclass options."""

        model = Part
        fields = [
            'pk',
            'bom_validated',
            'bom_checksum',
            'bom_checked_by',
            'bom_checked_by_detail',
            'bom_checked_date',
            'valid',
        ]

        read_only_fields = [
            'bom_validated',
            'bom_checksum',
            'bom_checked_by',
            'bom_checked_by_detail',
            'bom_checked_date',
        ]

    valid = serializers.BooleanField(
        write_only=True,
        default=False,
        required=False,
        label=_('Valid'),
        help_text=_('Validate entire Bill of Materials'),
    )

    bom_checked_by_detail = UserSerializer(
        source='bom_checked_by', many=False, read_only=True, allow_null=True
    )


class PartRequirementsSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for Part requirements."""

    class Meta:
        """Metaclass options."""

        model = Part
        fields = [
            'total_stock',
            'unallocated_stock',
            'can_build',
            'ordering',
            'building',
            'scheduled_to_build',
            'required_for_build_orders',
            'allocated_to_build_orders',
            'required_for_sales_orders',
            'allocated_to_sales_orders',
        ]

    total_stock = serializers.FloatField(read_only=True, label=_('Total Stock'))

    unallocated_stock = serializers.FloatField(
        source='available_stock', read_only=True, label=_('Available Stock')
    )

    can_build = serializers.FloatField(read_only=True, label=_('Can Build'))

    ordering = serializers.FloatField(
        source='on_order', read_only=True, label=_('On Order')
    )

    building = serializers.FloatField(
        read_only=True, label=_('In Production'), source='quantity_in_production'
    )

    scheduled_to_build = serializers.IntegerField(
        read_only=True, label=_('Scheduled to Build'), source='quantity_being_built'
    )

    required_for_build_orders = serializers.FloatField(
        source='required_build_order_quantity',
        read_only=True,
        label=_('Required for Build Orders'),
    )

    allocated_to_build_orders = serializers.FloatField(
        read_only=True,
        label=_('Allocated to Build Orders'),
        source='build_order_allocation_count',
    )

    required_for_sales_orders = serializers.FloatField(
        source='required_sales_order_quantity',
        read_only=True,
        label=_('Required for Sales Orders'),
    )

    allocated_to_sales_orders = serializers.SerializerMethodField(
        read_only=True, label=_('Allocated to Sales Orders')
    )

    def get_allocated_to_sales_orders(self, part) -&gt; float:
        """Return the allocated sales order quantity."""
        return part.sales_order_allocation_count(include_variants=True, pending=True)


class PartStocktakeSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    DataExportSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for the PartStocktake model."""

    class Meta:
        """Metaclass options."""

        model = PartStocktake
        fields = [
            'pk',
            'part',
            'part_name',
            'part_ipn',
            'part_description',
            'date',
            'item_count',
            'quantity',
            'cost_min',
            'cost_min_currency',
            'cost_max',
            'cost_max_currency',
            # Optional detail fields
            'part_detail',
        ]

        read_only_fields = ['date', 'user']

    def __init__(self, *args, **kwargs):
        """Custom initialization for PartStocktakeSerializer."""
        exclude_pk = kwargs.pop('exclude_pk', False)

        super().__init__(*args, **kwargs)

        if exclude_pk:
            self.fields.pop('pk', None)

    quantity = serializers.FloatField()

    cost_min = InvenTree.serializers.InvenTreeMoneySerializer(allow_null=True)
    cost_min_currency = InvenTree.serializers.InvenTreeCurrencySerializer()

    cost_max = InvenTree.serializers.InvenTreeMoneySerializer(allow_null=True)
    cost_max_currency = InvenTree.serializers.InvenTreeCurrencySerializer()

    part_name = serializers.CharField(
        source='part.name', read_only=True, label=_('Part Name')
    )

    part_ipn = serializers.CharField(
        source='part.IPN', read_only=True, allow_null=True, label=_('Part IPN')
    )

    part_description = serializers.CharField(
        source='part.description',
        read_only=True,
        allow_null=True,
        label=_('Part Description'),
    )

    part_detail = OptionalField(
        serializer_class=PartBriefSerializer,
        serializer_kwargs={
            'source': 'part',
            'read_only': True,
            'allow_null': True,
            'many': False,
            'pricing': False,
        },
        default_include=False,
    )

    def save(self):
        """Called when this serializer is saved."""
        data = self.validated_data

        # Add in user information automatically
        request = self.context.get('request')
        data['user'] = request.user if request else None
        return super().save()


class PartStocktakeGenerateSerializer(serializers.Serializer):
    """Serializer for generating PartStocktake entries."""

    class Meta:
        """Metaclass options."""

        fields = [
            'part',
            'category',
            'location',
            'generate_entry',
            'generate_report',
            'output',
        ]

    part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.all(),
        label=_('Part'),
        help_text=_(
            'Select a part to generate stocktake information for that part (and any variant parts)'
        ),
        required=False,
        allow_null=True,
    )

    category = serializers.PrimaryKeyRelatedField(
        queryset=PartCategory.objects.all(),
        label=_('Category'),
        help_text=_(
            'Select a category to include all parts within that category (and subcategories)'
        ),
        required=False,
        allow_null=True,
    )

    location = serializers.PrimaryKeyRelatedField(
        queryset=stock.models.StockLocation.objects.all(),
        label=_('Location'),
        help_text=_(
            'Select a location to include all parts with stock in that location (including sub-locations)'
        ),
        required=False,
        allow_null=True,
    )

    generate_entry = serializers.BooleanField(
        label=_('Generate Stocktake Entries'),
        help_text=_('Save stocktake entries for the selected parts'),
        write_only=True,
        required=False,
        default=False,
    )

    generate_report = serializers.BooleanField(
        label=_('Generate Report'),
        help_text=_('Generate a stocktake report for the selected parts'),
        write_only=True,
        required=False,
        default=False,
    )

    output = common.serializers.DataOutputSerializer(
        read_only=True, many=False, label=_('Output')
    )


@extend_schema_field(
    serializers.CharField(
        help_text=_('Select currency from available options')
        + '\n\n'
        + '\n'.join(
            f'* `{value}` - {label}'
            for value, label in common.currency.currency_code_mappings()
        )
        + "\n\nOther valid currencies may be found in the 'CURRENCY_CODES' global setting."
    )
)
class PartPricingCurrencySerializer(serializers.ChoiceField):
    """Serializer to allow annotating the schema to use String on currency fields."""


class PartPricingSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for Part pricing information."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartPricing
        fields = [
            'currency',
            'updated',
            'scheduled_for_update',
            'bom_cost_min',
            'bom_cost_max',
            'purchase_cost_min',
            'purchase_cost_max',
            'internal_cost_min',
            'internal_cost_max',
            'supplier_price_min',
            'supplier_price_max',
            'variant_cost_min',
            'variant_cost_max',
            'override_min',
            'override_min_currency',
            'override_max',
            'override_max_currency',
            'overall_min',
            'overall_max',
            'sale_price_min',
            'sale_price_max',
            'sale_history_min',
            'sale_history_max',
            'update',
        ]

    currency = InvenTree.serializers.InvenTreeCurrencySerializer(
        allow_null=True, read_only=True
    )

    updated = serializers.DateTimeField(allow_null=True, read_only=True)

    scheduled_for_update = serializers.BooleanField(read_only=True)

    # Custom serializers
    bom_cost_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    bom_cost_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    purchase_cost_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    purchase_cost_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    internal_cost_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    internal_cost_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    supplier_price_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    supplier_price_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    variant_cost_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    variant_cost_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    override_min = InvenTree.serializers.InvenTreeMoneySerializer(
        label=_('Minimum Price'),
        help_text=_('Override calculated value for minimum price'),
        allow_null=True,
        read_only=False,
        required=False,
    )

    override_min_currency = PartPricingCurrencySerializer(
        label=_('Minimum price currency'),
        read_only=False,
        required=False,
        choices=common.currency.currency_code_mappings(),
    )

    override_max = InvenTree.serializers.InvenTreeMoneySerializer(
        label=_('Maximum Price'),
        help_text=_('Override calculated value for maximum price'),
        allow_null=True,
        read_only=False,
        required=False,
    )

    override_max_currency = PartPricingCurrencySerializer(
        label=_('Maximum price currency'),
        read_only=False,
        required=False,
        choices=common.currency.currency_code_mappings(),
    )

    overall_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    overall_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    sale_price_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    sale_price_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    sale_history_min = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )
    sale_history_max = InvenTree.serializers.InvenTreeMoneySerializer(
        allow_null=True, read_only=True
    )

    update = serializers.BooleanField(
        write_only=True,
        label=_('Update'),
        help_text=_('Update pricing for this part'),
        default=False,
        required=False,
        allow_null=True,
    )

    def validate(self, data):
        """Validate supplied pricing data."""
        super().validate(data)

        # Check that override_min is not greater than override_max
        override_min = data.get('override_min', None)
        override_max = data.get('override_max', None)

        default_currency = common.currency.currency_code_default()

        if override_min is not None and override_max is not None:
            try:
                override_min = convert_money(override_min, default_currency)
                override_max = convert_money(override_max, default_currency)
            except MissingRate:
                raise ValidationError(
                    _(
                        f'Could not convert from provided currencies to {default_currency}'
                    )
                )

            if override_min &gt; override_max:
                raise ValidationError({
                    'override_min': _(
                        'Minimum price must not be greater than maximum price'
                    ),
                    'override_max': _(
                        'Maximum price must not be less than minimum price'
                    ),
                })

        return data

    def save(self):
        """Called when the serializer is saved."""
        super().save()

        data = self.validated_data

        if data.get('update', False):
            # Update part pricing
            pricing = self.instance
            pricing.update_pricing()


class PartSerialNumberSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for Part serial number information."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = Part
        fields = ['latest', 'next']

    latest = serializers.CharField(
        source='get_latest_serial_number', read_only=True, allow_null=True
    )
    next = serializers.CharField(source='get_next_serial_number', read_only=True)


class PartRelationSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for a PartRelated model."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartRelated
        fields = ['pk', 'part_1', 'part_1_detail', 'part_2', 'part_2_detail', 'note']

    part_1_detail = PartSerializer(source='part_1', read_only=True, many=False)
    part_2_detail = PartSerializer(source='part_2', read_only=True, many=False)


class PartStarSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for a PartStar object."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartStar
        fields = ['pk', 'part', 'partname', 'user', 'username']

    partname = serializers.CharField(source='part.full_name', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)


class BomItemSubstituteSerializer(InvenTree.serializers.InvenTreeModelSerializer):
    """Serializer for the BomItemSubstitute class."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = BomItemSubstitute
        fields = ['pk', 'bom_item', 'part', 'part_detail']
        # list_serializer_class = FilterableListSerializer

    part_detail = PartBriefSerializer(
        source='part', read_only=True, many=False, pricing=False
    )


@register_importer()
class BomItemSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    DataImportExportSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for BomItem object."""

    import_exclude_fields = ['quantity', 'validated', 'substitutes']

    export_exclude_fields = ['substitutes']

    export_child_fields = [
        'sub_part_detail.name',
        'sub_part_detail.IPN',
        'sub_part_detail.description',
    ]

    class Meta:
        """Metaclass defining serializer fields."""

        model = BomItem
        fields = [
            'part',
            'sub_part',
            'reference',
            'raw_amount',
            'quantity',
            'allow_variants',
            'inherited',
            'optional',
            'consumable',
            'setup_quantity',
            'attrition',
            'rounding_multiple',
            'note',
            'pk',
            'pricing_min',
            'pricing_max',
            'pricing_min_total',
            'pricing_max_total',
            'pricing_updated',
            'substitutes',
            'validated',
            # Annotated fields describing available quantity
            'available_stock',
            'available_substitute_stock',
            'available_variant_stock',
            'external_stock',
            # Annotated field describing quantity on order
            'on_order',
            # Annotated field describing quantity being built
            'building',
            # Annotate the total potential quantity we can build
            'can_build',
            # Optional detail fields
            'part_detail',
            'sub_part_detail',
            'category_detail',
        ]

    raw_amount = serializers.CharField(
        label=_('Amount'),
        help_text=_('Amount required for this item (can include units)'),
        required=False,
    )

    quantity = InvenTree.serializers.InvenTreeDecimalField(required=False)

    setup_quantity = InvenTree.serializers.InvenTreeDecimalField(required=False)

    attrition = InvenTree.serializers.InvenTreeDecimalField(required=False)

    rounding_multiple = InvenTree.serializers.InvenTreeDecimalField(
        required=False, allow_null=True
    )

    part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.filter(assembly=True),
        label=_('Assembly'),
        help_text=_('Select the parent assembly'),
    )

    substitutes = OptionalField(
        serializer_class=BomItemSubstituteSerializer,
        serializer_kwargs={
            'many': True,
            'read_only': True,
            'allow_null': True,
            'required': False,
        },
        default_include=False,
        filter_name='substitutes',
        prefetch_fields=[
            'substitutes',
            'substitutes__part',
            'substitutes__part__stock_items',
            'substitutes__part__pricing_data',
        ],
    )

    part_detail = OptionalField(
        serializer_class=PartBriefSerializer,
        serializer_kwargs={
            'source': 'part',
            'label': _('Assembly'),
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
    )

    sub_part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.filter(component=True),
        label=_('Component'),
        help_text=_('Select the component part'),
    )

    sub_part_detail = OptionalField(
        serializer_class=PartBriefSerializer,
        serializer_kwargs={
            'source': 'sub_part',
            'label': _('Component'),
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
    )

    category_detail = OptionalField(
        serializer_class=CategorySerializer,
        serializer_kwargs={
            'source': 'sub_part.category',
            'label': _('Category'),
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=False,
    )

    on_order = serializers.FloatField(
        label=_('On Order'), read_only=True, allow_null=True
    )

    building = serializers.FloatField(
        label=_('In Production'), read_only=True, allow_null=True
    )

    can_build = OptionalField(
        serializer_class=serializers.FloatField,
        serializer_kwargs={
            'label': _('Can Build'),
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
    )

    # Cached pricing fields
    pricing_min = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={
            'source': 'sub_part.pricing_data.overall_min',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    pricing_max = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={
            'source': 'sub_part.pricing_data.overall_max',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    pricing_min_total = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={'allow_null': True, 'read_only': True},
        default_include=True,
        filter_name='pricing',
    )

    pricing_max_total = OptionalField(
        serializer_class=InvenTree.serializers.InvenTreeMoneySerializer,
        serializer_kwargs={'allow_null': True, 'read_only': True},
        default_include=True,
        filter_name='pricing',
    )

    pricing_updated = OptionalField(
        serializer_class=serializers.DateTimeField,
        serializer_kwargs={
            'source': 'sub_part.pricing_data.updated',
            'allow_null': True,
            'read_only': True,
        },
        default_include=True,
        filter_name='pricing',
    )

    # Annotated fields for available stock
    available_stock = serializers.FloatField(
        label=_('Available Stock'), read_only=True, allow_null=True
    )

    available_substitute_stock = serializers.FloatField(read_only=True, allow_null=True)
    available_variant_stock = serializers.FloatField(read_only=True, allow_null=True)

    external_stock = serializers.FloatField(read_only=True, allow_null=True)

    def validate(self, data):
        """Validate the supplied data.

        Here, for legacy support, we intercept the 'quantity' field
        (if the 'raw_amount' field is not provided)
        """
        qty = data.pop('quantity', None)

        if 'raw_amount' not in data and qty is not None:
            data['raw_amount'] = qty

        # Check the raw_amount field is valid (this will raise a ValidationError if not)
        if raw_amount := data.get('raw_amount', None):
            try:
                # Check that the value is acceptable to the unit registry
                InvenTree.conversion.convert_value(raw_amount)
            except Exception:
                raise ValidationError({'raw_amount': _('Invalid quantity format')})

        return super().validate(data)

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the BomItem queryset with extra information.

        Annotations:
            available_stock: The amount of stock available for the sub_part Part object
        """
        """
        Construct an "available stock" quantity:
        available_stock = total_stock - build_order_allocations - sales_order_allocations
        """

        # Prefetch related fields
        queryset = queryset.prefetch_related(
            'part',
            'part__category',
            'part__stock_items',
            'sub_part',
            'sub_part__builds',
            'sub_part__category',
            'sub_part__pricing_data',
            'sub_part__stock_items',
            'sub_part__stock_items__allocations',
            'sub_part__stock_items__sales_order_allocations',
            'sub_part__stock_items__transfer_order_allocations',
        )

        # Annotate with the 'total pricing' information based on unit pricing and quantity
        queryset = queryset.annotate(
            pricing_min_total=ExpressionWrapper(
                F('quantity') * F('sub_part__pricing_data__overall_min'),
                output_field=models.DecimalField(),
            ),
            pricing_max_total=ExpressionWrapper(
                F('quantity') * F('sub_part__pricing_data__overall_max'),
                output_field=models.DecimalField(),
            ),
        )

        ref = 'sub_part__'

        # Annotate with the total "on order" amount for the sub-part
        queryset = queryset.annotate(
            on_order=part_filters.annotate_on_order_quantity(ref)
        )

        # Annotate with the total "building" amount for the sub-part
        queryset = queryset.annotate(
            building=part_filters.annotate_in_production_quantity(ref)
        )

        # Calculate 'external_stock'
        queryset = queryset.annotate(
            external_stock=part_filters.annotate_total_stock(
                reference=ref, filter=Q(location__external=True)
            )
        )

        # Annotate available stock and "can_build" quantities
        queryset = part_filters.annotate_bom_item_can_build(queryset)

        return queryset


@register_importer()
class CategoryParameterTemplateSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    DataImportExportSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Serializer for the PartCategoryParameterTemplate model."""

    class Meta:
        """Metaclass defining serializer fields."""

        model = PartCategoryParameterTemplate
        fields = [
            'pk',
            'category',
            'category_detail',
            'template',
            'template_detail',
            'default_value',
        ]

    template_detail = OptionalField(
        serializer_class=common.serializers.ParameterTemplateSerializer,
        serializer_kwargs={'source': 'template', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['template'],
    )

    category_detail = OptionalField(
        serializer_class=CategorySerializer,
        serializer_kwargs={
            'source': 'category',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['category'],
    )


class PartCopyBOMSerializer(serializers.Serializer):
    """Serializer for copying a BOM from another part."""

    class Meta:
        """Metaclass defining serializer fields."""

        fields = [
            'part',
            'remove_existing',
            'copy_substitutes',
            'include_inherited',
            'skip_invalid',
        ]

    part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.all(),
        many=False,
        required=True,
        allow_null=False,
        label=_('Part'),
        help_text=_('Select part to copy BOM from'),
    )

    def validate_part(self, part):
        """Check that a 'valid' part was selected."""
        return part

    remove_existing = serializers.BooleanField(
        label=_('Remove Existing Data'),
        help_text=_('Remove existing BOM items before copying'),
        default=True,
    )

    include_inherited = serializers.BooleanField(
        label=_('Include Inherited'),
        help_text=_('Include BOM items which are inherited from templated parts'),
        default=False,
    )

    skip_invalid = serializers.BooleanField(
        label=_('Skip Invalid Rows'),
        help_text=_('Enable this option to skip invalid rows'),
        default=False,
    )

    copy_substitutes = serializers.BooleanField(
        label=_('Copy Substitute Parts'),
        help_text=_('Copy substitute parts when duplicate BOM items'),
        default=True,
    )

    def save(self):
        """Actually duplicate the BOM."""
        base_part = self.context['part']

        data = self.validated_data

        base_part.copy_bom_from(
            data['part'],
            clear=data.get('remove_existing', True),
            skip_invalid=data.get('skip_invalid', False),
            include_inherited=data.get('include_inherited', False),
            copy_substitutes=data.get('copy_substitutes', True),
        )

  </file>
</source_code_and_diff>

<analytics_documentation>
  <module>part</module>
  <endpoints>
    <endpoint>category/</endpoint>
    <endpoint>tree/</endpoint>
    <endpoint>parameters/</endpoint>
    <endpoint>&lt;int:pk&gt;/</endpoint>
    <endpoint>test-template/</endpoint>
    <endpoint>sale-price/</endpoint>
    <endpoint>internal-price/</endpoint>
    <endpoint>related/</endpoint>
    <endpoint>stocktake/</endpoint>
    <endpoint>generate/</endpoint>
    <endpoint>thumbs/</endpoint>
    <endpoint>serial-numbers/</endpoint>
    <endpoint>requirements/</endpoint>
    <endpoint>bom-copy/</endpoint>
    <endpoint>bom-validate/</endpoint>
    <endpoint>pricing/</endpoint>
    <endpoint>substitute/</endpoint>
    <endpoint>validate/</endpoint>
    <endpoint>parent</endpoint>
    <endpoint>cascade</endpoint>
    <endpoint>top_level</endpoint>
    <endpoint>depth</endpoint>
    <endpoint>

    OPTIONS = [InvenTreeOutputOption(flag=</endpoint>
    <endpoint>starred</endpoint>
    <endpoint>
        serializer = part_serializers.CategoryDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delete_parts = str2bool(serializer.validated_data.get(</endpoint>
    <endpoint>delete_child_categories</endpoint>
    <endpoint>category</endpoint>
    <endpoint>fetch_parent</endpoint>
    <endpoint>include_inherited</endpoint>
    <endpoint>pk</endpoint>
    <endpoint>valid</endpoint>
    <endpoint>

    OPTIONS = [
        InvenTreeOutputOption(
            </endpoint>
    <endpoint>
        from common.models import DataOutput
        from part.stocktake import perform_stocktake

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        part = data.get(</endpoint>
    <endpoint>location</endpoint>
    <endpoint>generate_report</endpoint>
    <endpoint>generate_entry</endpoint>
    <endpoint>: report_output,
        }

        output_serializer = part_serializers.PartStocktakeGenerateSerializer(result)

        return Response(output_serializer.data)


class BomFilter(FilterSet):
    </endpoint>
    <endpoint>

    OPTIONS = [
        InvenTreeOutputOption(</endpoint>
    <endpoint>),
        help_text=_(</endpoint>
    <endpoint>),
        required=True,
    )

    delete_parts = serializers.BooleanField(
        label=_(</endpoint>
  </endpoints>
  <models>
    <model>PartCategoryParameterTemplate</model>
    <model>PartStocktake</model>
    <model>PartStar</model>
    <model>PartCategoryStar</model>
    <model>PartTestTemplate</model>
    <model>BomItemSubstitute</model>
    <model>PartRelated</model>
  </models>
  <stack>
    <language>python</language>
    <framework>django</framework>
    <test_framework>pytest</test_framework>
    <build_tool>pip</build_tool>
  </stack>
</analytics_documentation>
