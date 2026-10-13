"""Read Apple NAND identity from the selected device's IORegistry ancestry."""
import ctypes
import platform
import plistlib
import re


def _text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value.strip(" \t\r\n\0") if isinstance(value, str) else None


def describe_nand(properties, controller=None):
    # 'Vendor Name' and the NVMe PCI vendor ID describe the controller/device,
    # not the NAND. Only this specific driver-reported field is used.
    raw = _text(properties.get("vendor-name"))
    aliases = {"samsung": "Samsung", "toshiba": "Toshiba / Kioxia", "kioxia": "Kioxia",
               "hynix": "SK hynix", "skhynix": "SK hynix", "sk hynix": "SK hynix",
               "micron": "Micron", "ymtc": "YMTC", "sandisk": "SanDisk"}
    return {"status": "reported" if raw else "not_exposed",
            "supplier": aliases.get(raw.casefold(), raw) if raw else None,
            "reported_name": raw, "controller": controller,
            "nand_model": _text(properties.get("nand-marketing-name")),
            "bits_per_cell": properties.get("default-bits-per-cell"),
            "source": "IORegistry: Controller Characteristics.vendor-name" if raw else None}


def _frameworks():
    io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    signatures = [
        (io, "IORegistryEntryFromPath", ctypes.c_uint, [ctypes.c_uint, ctypes.c_char_p]),
        (io, "IOBSDNameMatching", ctypes.c_void_p, [ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p]),
        (io, "IOServiceGetMatchingService", ctypes.c_uint, [ctypes.c_uint, ctypes.c_void_p]),
        (io, "IORegistryEntryGetParentEntry", ctypes.c_int, [ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]),
        (io, "IORegistryEntryCreateCFProperty", ctypes.c_void_p, [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]),
        (io, "IOObjectGetClass", ctypes.c_int, [ctypes.c_uint, ctypes.c_void_p]),
        (io, "IOObjectRelease", ctypes.c_int, [ctypes.c_uint]),
        (cf, "CFStringCreateWithCString", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]),
        (cf, "CFPropertyListCreateData", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_ulong, ctypes.c_void_p]),
        (cf, "CFDataGetLength", ctypes.c_long, [ctypes.c_void_p]),
        (cf, "CFDataGetBytePtr", ctypes.c_void_p, [ctypes.c_void_p]),
        (cf, "CFRelease", None, [ctypes.c_void_p]),
    ]
    for library, name, result, arguments in signatures:
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
    return io, cf


def _read_property(io, cf, entry):
    key = cf.CFStringCreateWithCString(None, b"Controller Characteristics", 0x08000100)
    if not key:
        raise OSError("Cannot create IORegistry property key.")
    value = data = None
    try:
        value = io.IORegistryEntryCreateCFProperty(entry, key, None, 0)
        if not value:
            return None
        data = cf.CFPropertyListCreateData(None, value, 200, 0, None)
        if not data:
            raise OSError("Cannot decode IORegistry property.")
        size = cf.CFDataGetLength(data)
        if not 0 < size <= 1024 * 1024:
            raise OSError("Invalid IORegistry property size.")
        result = plistlib.loads(ctypes.string_at(cf.CFDataGetBytePtr(data), size))
        return result if isinstance(result, dict) else None
    finally:
        for reference in (data, value, key):
            if reference:
                cf.CFRelease(reference)


def apple_nand_info(device):
    if platform.system() != "Darwin":
        return {"status": "not_applicable", "supplier": None}
    # Resolve only the selected device, then walk its parents. Never select the
    # first Apple controller: multiple controllers/SSDs can be connected.
    bsd = re.fullmatch(r"/dev/r?(disk\d+)", device or "")
    registry = isinstance(device, str) and device.startswith("IOService:/") and "\0" not in device
    if not bsd and not registry:
        return {"status": "unavailable", "supplier": None, "reason": "Unrecognized physical device path."}
    entry = 0
    try:
        io, cf = _frameworks()
        if registry:
            entry = io.IORegistryEntryFromPath(0, device.encode("utf-8"))
        else:
            matching = io.IOBSDNameMatching(0, 0, bsd.group(1).encode("ascii"))
            entry = io.IOServiceGetMatchingService(0, matching) if matching else 0
        if not entry:
            raise OSError("Selected device is unavailable in IORegistry.")
        for _ in range(32):
            properties = _read_property(io, cf, entry)
            if properties is not None:
                name = ctypes.create_string_buffer(128)
                controller = name.value.decode("utf-8", errors="replace") if io.IOObjectGetClass(entry, name) == 0 else None
                return describe_nand(properties, controller)
            parent = ctypes.c_uint()
            status = io.IORegistryEntryGetParentEntry(entry, b"IOService", ctypes.byref(parent))
            if status or not parent.value:
                break
            io.IOObjectRelease(entry)
            entry = parent.value
        return {"status": "not_exposed", "supplier": None}
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return {"status": "unavailable", "supplier": None, "reason": str(exc)}
    finally:
        if entry:
            io.IOObjectRelease(entry)
