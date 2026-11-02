# sub8's proof run: any read of a per-file snapshot raises.
import sys
sys.path.insert(0, "/home/cc/node_ops/diag/kcase/v2")
import drain_order


def _no(self, path):
    raise RuntimeError("per-file snapshot opened: " + str(path))


drain_order.Snap.__init__ = _no
