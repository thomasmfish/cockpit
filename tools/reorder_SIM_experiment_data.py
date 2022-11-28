import dataclasses
import sys
import os.path
from glob import glob

from cockpit.experiment import structuredIllumination
from cockpit.util.datadoc import DataDoc


@dataclasses.dataclass
class FakeSIExperiment:
    savePath: str
    collectionOrder: str
    numAngles: int
    numZSlices: int
    numPhases: int


if __name__ == "__main__":
    args = sys.argv[1:]
    dirs = set(d for d in args if os.path.isdir(d))
    files = set(p for p in args if os.path.isfile(p))
    if dirs:
        for d in dirs:
            files.update([p for p in glob(os.path.join(d, "*")) if os.path.isfile(p)])

    for fpath in files:
        doc = DataDoc(fpath)
        n_z = int(doc.getNPlanes() / 2 / 3 /5) # 2 channels, 3 angles, 5 phases
        del doc # workaround bug in datadoc
        experiment = FakeSIExperiment(fpath, "Z, Angle, Phase", 3, n_z, 5)
        try:
            print('doing ', fpath)
            structuredIllumination.SIExperiment.reorder_img_file(experiment)
        except Exception:
            print("Processing for %s failed" % fpath)
        except:
            print("Processing was stopped midway through %s" % fpath)
            break
