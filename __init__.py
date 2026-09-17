def classFactory(iface):
    from .nisar_processor import NISARProcessor
    return NISARProcessor(iface)
