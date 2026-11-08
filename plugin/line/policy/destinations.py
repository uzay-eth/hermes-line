"""Fail-closed destination parsing. This module never dials or normalizes input."""
from __future__ import annotations
import re
from dataclasses import dataclass

# Conservative geographic US routing metadata. Unknown/new NPAs fail closed;
# NANP country code +1 alone does not establish a US destination.
US_AREA_CODES = frozenset('''201 202 203 205 206 207 208 209 210 212 213 214 215 216 217
218 219 220 223 224 225 227 228 229 231 234 239 240 248 251 252 253 254 256 260
262 267 269 270 272 274 276 279 281 301 302 303 304 305 307 308 309 310 312 313
314 315 316 317 318 319 320 321 323 325 326 330 331 332 334 336 337 339 341 346
347 350 351 352 360 361 363 364 380 385 386 401 402 404 405 406 407 408 409 410
412 413 414 415 417 419 423 424 425 430 432 434 435 440 442 443 445 447 448 458
463 464 469 470 475 478 479 480 484 501 502 503 504 505 507 508 509 510 512 513
515 516 517 518 520 530 531 534 539 540 541 551 559 561 562 563 564 567 570 571
572 573 574 575 580 582 585 586 601 602 603 605 606 607 608 609 610 612 614 615
616 617 618 619 620 623 626 628 629 630 631 636 640 641 646 650 651 656 657 659
660 661 662 667 669 678 680 681 682 701 702 703 704 706 707 708 712 713 714 715
716 717 718 719 720 724 725 726 727 730 731 732 734 737 740 743 747 754 757 760
762 763 765 769 770 771 772 773 774 775 779 781 785 786 801 802 803 804 805 806
808 810 812 813 814 815 816 817 818 820 826 828 830 831 832 838 839 840 843 845
847 848 850 854 856 857 858 859 860 862 863 864 865 870 872 878 901 903 904 906
907 908 909 910 912 913 914 915 916 917 918 919 920 925 928 929 930 931 934 936
937 938 940 941 945 947 948 949 951 952 954 956 959 970 971 972 973 978 979 980
983 984 985 989'''.split())
TOLL_FREE_AREA_CODES = frozenset(('800', '833', '844', '855', '866', '877', '888'))
PREMIUM_AREA_CODES = frozenset(('900', '976'))

@dataclass(frozen=True)
class DestinationDecision:
    allowed: bool
    e164: str | None
    reason: str


def _hard_reason(raw):
    if not isinstance(raw, str) or len(raw) > 64:
        return 'invalid_destination'
    # No trimming, Unicode digits, URI parsing, or removal of punctuation.
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', raw):
        return 'noncanonical_or_service_code'
    if not re.fullmatch(r'\+1[2-9][0-9]{2}[2-9][0-9]{6}', raw):
        return 'unsupported_country_or_invalid_number'
    if raw[2:5] in PREMIUM_AREA_CODES or raw[5:8] == '976':
        return 'premium_or_pay_per_call'
    if raw[3:5] == '11' or raw[6:8] == '11':
        return 'service_exchange'
    if raw[2:5] not in US_AREA_CODES | TOLL_FREE_AREA_CODES:
        return 'unsupported_us_area_code'
    return None


def _normalize_blocked(raw):
    """Normalize config entries only; never reinterpret a dial instruction.

    Accept ASCII spaces/dashes, ten-digit US national numbers, eleven digits
    starting with 1, or explicit +country E.164. No URIs, extensions or wildcards.
    International entries may be blocked even though dialing them is unsupported.
    """
    if not isinstance(raw, str) or len(raw) > 64:
        raise ValueError('invalid blocked destination')
    value = raw.strip(' ')
    if not re.fullmatch(r'\+?[0-9][0-9 -]*[0-9]', value):
        raise ValueError('invalid blocked destination')
    value = value.replace(' ', '').replace('-', '')
    if len(value) == 10 and value.isascii() and value.isdigit():
        value = '+1' + value
    elif len(value) == 11 and value.startswith('1'):
        value = '+' + value
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValueError('blocked destination must be a full phone number')
    return value


def check_destination(raw: str, settings) -> DestinationDecision:
    reason = _hard_reason(raw)
    if reason:
        return DestinationDecision(False, None, reason)
    blocked = getattr(settings, 'blocked_destinations', ())
    if not isinstance(blocked, (list, tuple)):
        return DestinationDecision(False, None, 'invalid_blocked_destinations')
    try:
        blocked = tuple(_normalize_blocked(value) for value in blocked)
    except ValueError:
        return DestinationDecision(False, None, 'invalid_blocked_destinations')
    if raw in blocked:
        return DestinationDecision(False, None, 'blocked_destination')
    allowlist_only = getattr(settings, 'allowlist_only', True)
    if type(allowlist_only) is not bool:
        return DestinationDecision(False, None, 'invalid_allowlist_mode')
    if allowlist_only and raw not in getattr(settings, 'allowed_destinations', ()):
        return DestinationDecision(False, None, 'not_allowlisted')
    return DestinationDecision(True, raw, 'approved_destination_requires_consent')
