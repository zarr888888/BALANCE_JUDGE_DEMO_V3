"""BALANCE integration boundary, simulation planning and telemetry reception."""
__version__ = '1.0.0'

def create_blueprint(service, *, dashboard_key, device_keys=None, url_prefix='/api/balance/v1'):
    from .api import create_blueprint as make
    return make(service, dashboard_key=dashboard_key, device_keys=device_keys or {}, url_prefix=url_prefix)

def create_service(state_dir):
    from .service import BalanceService
    return BalanceService(state_dir)
