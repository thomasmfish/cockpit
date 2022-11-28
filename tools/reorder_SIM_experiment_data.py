import dataclasses
import sys

from cockpit.experiment import structuredIllumination
from cockpit.util.datadoc import DataDoc


@dataclasses.dataclass
class FakeSIExperiment:
    savePath: str
    collectionOrder: str
    numAngles: int
    numZSlices: int
    numPhases: int


for fpath in sys.argv[1:]:
    doc = DataDoc(fpath)
    n_z = int(doc.getNPlanes() / 2 / 3 /5) # 2 channels, 3 angles, 5 phases
    del doc # workaround bug in datadoc
    experiment = FakeSIExperiment(fpath, "Z, Angle, Phase", 3, n_z, 5)
    print('doing ', fpath)
    structuredIllumination.SIExperiment.reorder_img_file(experiment)
