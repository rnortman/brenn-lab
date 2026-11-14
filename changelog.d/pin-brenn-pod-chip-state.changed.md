- The recorder and reader pinned from brenn-pod moved to commit `5a55610`.
  The recorder now reads an XVF3800's own state over USB before it starts a
  capture: it opens the device's `/dev/bus/usb/<bus>/<address>` node
  read-write and only reads from the device. An account that may not open
  the node still records, and the manifest says `unavailable`; brenn-pod
  ships a udev rule that grants access to the node.
