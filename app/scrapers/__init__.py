from app.scrapers.apache_torrent import ApacheTorrentScraper
from app.scrapers.archive_org import ArchiveOrgScraper
from app.scrapers.brazuca_addon import BrazucaAddonScraper
from app.scrapers.comando_filmes import ComandoFilmesScraper
from app.scrapers.hdr_torrent import HDRTorrentScraper
from app.scrapers.micoleao import MicoLeaoScraper
from app.scrapers.rutracker import RuTrackerScraper
from app.scrapers.torrent_1337x import Torrent1337xScraper
from app.scrapers.torrent_galaxy import TorrentGalaxyScraper
from app.scrapers.yts import YTSScraper

__all__ = [
    "ApacheTorrentScraper",
    "ArchiveOrgScraper",
    "ComandoFilmesScraper",
    "HDRTorrentScraper",
    "MicoLeaoScraper",
    "BrazucaAddonScraper",
    "YTSScraper",
    "TorrentGalaxyScraper",
    "Torrent1337xScraper",
    "RuTrackerScraper",
]
