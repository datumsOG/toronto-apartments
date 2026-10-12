"""Registered adapters. Real runs use ADAPTERS; --demo runs the demo and nothing else."""
from collect.adapters.demo import DemoAdapter
from collect.adapters.gateway import SterlingKaramarAdapter
from collect.adapters.goldseal import GoldSealAdapter
from collect.adapters.homestead import HomesteadAdapter
from collect.adapters.hrpm import HrpmAdapter
from collect.adapters.akelius import AkeliusAdapter
from collect.adapters.berkley import BerkleyAdapter
from collect.adapters.capreit import CapreitAdapter
from collect.adapters.medallion import MedallionAdapter
from collect.adapters.metcap import MetcapAdapter
from collect.adapters.quadreal import QuadrealAdapter
from collect.adapters.minto import MintoAdapter
from collect.adapters.padmapper import PadmapperAdapter
from collect.adapters.rentseeker import RentseekerAdapter
from collect.adapters.rpms import RpmsAdapter
from collect.adapters.tricon import TriconAdapter
from collect.adapters.viewit import ViewitAdapter
from collect.adapters.liftapi import LIFT

ADAPTERS = [HomesteadAdapter, *LIFT, MetcapAdapter, CapreitAdapter, RpmsAdapter, TriconAdapter, MintoAdapter, QuadrealAdapter, AkeliusAdapter, SterlingKaramarAdapter, MedallionAdapter, GoldSealAdapter, BerkleyAdapter, HrpmAdapter, RentseekerAdapter, ViewitAdapter, PadmapperAdapter]
DEMO = [DemoAdapter]


def registered(demo=False):
    # --demo runs the invented data alone: it must never reach a real landlord's site.
    return [cls() for cls in (DEMO if demo else ADAPTERS)]
