import datetime
from rest_framework import viewsets, status
from rest_framework.response import Response
from ares.api.serializers.v1.example import ExampleSerializer
from drf_spectacular.utils import extend_schema


# Dummy data
DUMMY_DATA = [
    {
        "id": 1,
        "name": "Example 1",
        "description": "This is the first example item.",
        "created_at": datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
    },
    {
        "id": 2,
        "name": "Example 2",
        "description": "This is the second example item.",
        "created_at": datetime.datetime(2024, 1, 2, 12, 0, 0, tzinfo=datetime.UTC),
    },
]


class ExampleViewSet(viewsets.ViewSet):
    """
    A simple ViewSet for viewing examples with dummy data.
    """

    @extend_schema(responses=ExampleSerializer(many=True))
    def list(self, _):
        serializer = ExampleSerializer(DUMMY_DATA, many=True)
        return Response(serializer.data)

    @extend_schema(responses=ExampleSerializer)
    def retrieve(self, _, pk: int = None):
        try:
            item = next(item for item in DUMMY_DATA if item["id"] == int(pk))
        except (StopIteration, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ExampleSerializer(item)
        return Response(serializer.data)
