
from django.conf import settings

_configured = False


def configure_observability():
    global _configured
    if _configured or not getattr(settings, "OTEL_ENABLED", False):
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        "service.name": getattr(settings, "OTEL_SERVICE_NAME", "mmr-real-estate-backend"),
        "deployment.environment": getattr(settings, "ENVIRONMENT", "development"),
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=getattr(settings, "OTEL_EXPORTER_ENDPOINT", "http://localhost:4318/v1/traces"))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    DjangoInstrumentor().instrument()
    _configured = True
