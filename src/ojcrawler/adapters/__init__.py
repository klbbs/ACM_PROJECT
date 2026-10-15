from .atcoder import AtCoder
from .codeforces import Codeforces
from .luogu import Luogu
from .nowcoder import Nowcoder

ADAPTERS = {a.platform: a for a in (Codeforces, Luogu, Nowcoder, AtCoder)}
