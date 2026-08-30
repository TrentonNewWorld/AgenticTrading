from fastapi import APIRouter

from dashboard.backend.api.routers.agent_versions import router as agent_versions_router
from dashboard.backend.api.routers.agents import router as agents_router
from dashboard.backend.api.routers.algo import router as algo_router
from dashboard.backend.api.routers.admin_users import router as admin_users_router
from dashboard.backend.api.routers.execution_settings import router as execution_settings_router
from dashboard.backend.api.auth import router as auth_router
from dashboard.backend.api.routers.connections import router as connections_router
from dashboard.backend.api.routers.discord import router as discord_router
from dashboard.backend.api.routers.environments import router as environments_router
from dashboard.backend.api.routers.external_backtest import router as external_backtest_router
from dashboard.backend.api.health import router as health_router
from dashboard.backend.api.routers.leaderboard import router as leaderboard_router
from dashboard.backend.api.routers.manual10 import router as manual10_router
from dashboard.backend.api.routers.mission_control import router as mission_control_router
from dashboard.backend.api.routers.news import router as news_router
from dashboard.backend.api.routers.portfolio import router as portfolio_router
from dashboard.backend.api.routers.runs import router as runs_router
from dashboard.backend.api.routers.strategies import router as strategies_router
from dashboard.backend.api.routers.wallets import router as wallets_router
from dashboard.backend.api.routers.crypto_leaderboard import router as crypto_leaderboard_router
from dashboard.backend.api.routers.crypto_manual import router as crypto_manual_router
from dashboard.backend.api.routers.crypto_strategy_catalog import router as crypto_strategy_catalog_router
from dashboard.backend.api.routers.forex_leaderboard import router as forex_leaderboard_router
from dashboard.backend.api.routers.forex_manual import router as forex_manual_router
from dashboard.backend.api.routers.forex_strategy_catalog import router as forex_strategy_catalog_router
from dashboard.backend.api.routers.futures_leaderboard import router as futures_leaderboard_router
from dashboard.backend.api.routers.futures_manual import router as futures_manual_router
from dashboard.backend.api.routers.futures_strategy_catalog import router as futures_strategy_catalog_router
from dashboard.backend.api.routers.options_leaderboard import router as options_leaderboard_router
from dashboard.backend.api.routers.options_manual import router as options_manual_router
from dashboard.backend.api.routers.options_strategy_catalog import router as options_strategy_catalog_router
from dashboard.backend.api.routers.prediction import router as prediction_router
from dashboard.backend.api.routers.strategy_catalog import router as strategy_catalog_router
from dashboard.backend.api.routers.strategy_testing import router as strategy_testing_router
from dashboard.backend.api.routers.robinhood_live import router as robinhood_router
from dashboard.backend.api.v2.router import v2_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(admin_users_router)
api_router.include_router(execution_settings_router)
api_router.include_router(algo_router)
api_router.include_router(agents_router)
api_router.include_router(discord_router)
api_router.include_router(agent_versions_router)
api_router.include_router(external_backtest_router)
api_router.include_router(runs_router)
api_router.include_router(environments_router)
api_router.include_router(leaderboard_router)
api_router.include_router(manual10_router)
api_router.include_router(mission_control_router)
api_router.include_router(wallets_router)
api_router.include_router(strategies_router)
api_router.include_router(strategy_catalog_router)
api_router.include_router(options_strategy_catalog_router)
api_router.include_router(options_leaderboard_router)
api_router.include_router(options_manual_router)
api_router.include_router(futures_manual_router)
api_router.include_router(futures_strategy_catalog_router)
api_router.include_router(futures_leaderboard_router)
api_router.include_router(forex_manual_router)
api_router.include_router(forex_strategy_catalog_router)
api_router.include_router(forex_leaderboard_router)
api_router.include_router(crypto_manual_router)
api_router.include_router(crypto_strategy_catalog_router)
api_router.include_router(crypto_leaderboard_router)
api_router.include_router(prediction_router)
api_router.include_router(strategy_testing_router)
api_router.include_router(portfolio_router)
api_router.include_router(news_router)
api_router.include_router(robinhood_router)
api_router.include_router(connections_router)
api_router.include_router(v2_router)
