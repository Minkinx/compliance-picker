import os
import sys
from colorama import Fore


class Color:
    @staticmethod
    def print_focus(msg):
        print(f"{Fore.YELLOW}[*] {msg}{Fore.RESET}")

    @staticmethod
    def print_success(msg):
        print(f"{Fore.LIGHTGREEN_EX}[+] {msg}{Fore.RESET}")

    @staticmethod
    def print_failed(msg):
        print(f"{Fore.LIGHTRED_EX}[-] {msg}{Fore.RESET}")

    @staticmethod
    def print(msg):
        from pprint import pprint
        pprint(msg)


def getenv(key, pick=False):
    val = os.getenv(f"PICKER_{key}" if pick else key)
    return val
