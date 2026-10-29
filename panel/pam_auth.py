"""Root password verification using the host's PAM policy; no shadow-file parsing."""
import ctypes as C
import ctypes.util


class Message(C.Structure):
    _fields_ = [('style', C.c_int), ('text', C.c_char_p)]


class Response(C.Structure):
    _fields_ = [('response', C.c_void_p), ('code', C.c_int)]


CALLBACK = C.CFUNCTYPE(C.c_int, C.c_int, C.POINTER(C.POINTER(Message)),
                      C.POINTER(C.POINTER(Response)), C.c_void_p)


class Conversation(C.Structure):
    _fields_ = [('callback', CALLBACK), ('data', C.c_void_p)]


def authenticate(username, password):
    if username != 'root' or not password or '\x00' in password:
        return False
    pam = C.CDLL(ctypes.util.find_library('pam') or 'libpam.so.0')
    libc = C.CDLL(None)
    libc.calloc.argtypes = [C.c_size_t, C.c_size_t]
    libc.calloc.restype = C.c_void_p
    libc.strdup.argtypes = [C.c_char_p]
    libc.strdup.restype = C.c_void_p
    libc.free.argtypes = [C.c_void_p]

    @CALLBACK
    def converse(count, messages, output, _):
        if count < 1 or count > 16:
            return 19  # PAM_CONV_ERR
        ptr = libc.calloc(count, C.sizeof(Response))
        if not ptr:
            return 5
        responses = C.cast(ptr, C.POINTER(Response))
        for index in range(count):
            style = messages[index].contents.style
            if style in (1, 2):  # PAM_PROMPT_ECHO_OFF / ON
                value = password if style == 1 else username
                responses[index].response = libc.strdup(value.encode())
                if not responses[index].response:
                    for j in range(index):
                        libc.free(responses[j].response)
                    libc.free(ptr)
                    return 5
            elif style not in (3, 4):  # error / informational messages
                for j in range(index):
                    libc.free(responses[j].response)
                libc.free(ptr)
                return 19
        output[0] = responses  # PAM owns and frees these allocations.
        return 0

    pam.pam_start.argtypes = [C.c_char_p, C.c_char_p, C.POINTER(Conversation), C.POINTER(C.c_void_p)]
    pam.pam_authenticate.argtypes = [C.c_void_p, C.c_int]
    pam.pam_acct_mgmt.argtypes = [C.c_void_p, C.c_int]
    pam.pam_end.argtypes = [C.c_void_p, C.c_int]
    handle = C.c_void_p()
    conversation = Conversation(converse, None)
    result = pam.pam_start(b'openvpn-panel', b'root', C.byref(conversation), C.byref(handle))
    if result != 0:
        return False
    try:
        result = pam.pam_authenticate(handle, 0x0001)  # PAM_DISALLOW_NULL_AUTHTOK
        if result == 0:
            result = pam.pam_acct_mgmt(handle, 0)
        return result == 0
    finally:
        pam.pam_end(handle, result)
