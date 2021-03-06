import collections
import concurrent.futures
import logging
import mimetypes
import os
import sys
import threading
import time
from urllib.parse import urljoin, urlparse
from typing import List, Set, Dict, Tuple, Optional, Callable

import requests

thread_local = threading.local()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def remove_query_from_url(url):
    """ 
    This function remove the query term in the url.
    
    Parameters: 
        url (string)

    Returns: 
        the processed url (string)
    """
    return urljoin(url, urlparse(url).path) 


def is_url_image(url):  
    """ 
    This function detects if the given url is an image.
    
    Parameters: 
        url (string)

    Returns: 
        whether it is an image url or not(boolean)
    """
    url = remove_query_from_url(url)  
    mimetype, _ = mimetypes.guess_type(url)
    return (mimetype and mimetype.startswith("image"))


def get_session():
    """ 
    This function generates one session for each thread:
    
    Parameters: 
        None

    Returns: 
        None
    """
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session


def get_thread_local_err_cntr():
    if not hasattr(thread_local, "err_cntr"):
        thread_local.err_cntr = 0
    return thread_local.err_cntr


def increment_thread_local_err_cntr():
    if not hasattr(thread_local, "err_cntr"):
        thread_local.err_cntr = 0
    thread_local.err_cntr += 1


def set_to_zero_thread_local_err_cntr():
    thread_local.err_cntr = 0


class URLDownloader_v1:
    """ 
    This is a class for downloading a batch of urls via http connection.
      
    Attributes: 
        url_list (list): a list of url to download.
        out_path (string): the path to the output folder /
        outpath_list (list): appending the outname with outpath.
        num_thread (int): the number of thread used for download.
        err_tolerance_num (int): the number of error tolerance for downloading.
        stop_interval (int): the secs to stop after error number exceeds the  err_tolerance_num
        time_out_for_GET (int): the time limit for http GET
        http_headers (dict): the header for http.
        outname_list (list): the list for the output file name. The default behaviour is using the file name in the url. If this is specified, it will overwrite the default name.
        err_cnter (int): counter for counting consecutive errors.
        log_file (string): a file name for logging, saving inside the out_path.
        _errs_cnter_lock (RLock): avoid race condition. this lock is for err_cnter
        __log_lock (Lock): avoid race condition. this lock is for log_file
    """
    def __init__(self, url_list,
                 out_path,
                 num_thread=4,
                 err_tolerance_num=1000,
                 stop_interval=0,
                 time_out_for_GET=600,
                 http_headers={},
                 remove_dup_img=False,
                 outname_list=None,
                 verbose=True
                 ):
        """ 
        The constructor for URLDownloader Class. It saves the parameters as attributes, set some attributes, and call update_downloading_status
        
        Parameters: 
            url_list (list): a list of url to download.
            out_path (string): the path to the output folder 
            num_thread (int): the number of thread used for download.
            err_tolerance_num (int): the number of error tolerance for downloading.
            stop_interval (int): the secs to stop after error number exceeds the  err_tolerance_num
            time_out_for_GET (int): the time limit for http GET
            http_headers (dict): the header for http.
            remove_dup_img (boolean): whether to remove the same image with different urls.
            outname_list (list): the list for the output file name. The default behaviour is using the file name in the url. If this is specified, it will overwrite the default name.
                    
        Returns: 
            The URLDownloader object
        """
        self.out_path = out_path
        if outname_list:
            assert(len(url_list) == len(outname_list))
            url2oname = {url: oname for url, oname in zip(url_list, outname_list)}
            url_list = [k for k in url2oname.keys()]
            outname_list = [v for v in url2oname.values()]
        else:
            url_list = list(set(url_list))
        if outname_list:
            self.outpath_list = [os.path.join(out_path, name) for name in outname_list]
        else:
            self.outpath_list = [self.get_outpath_from_url(i) for i in url_list]
        self.url_list = url_list
        self.num_thread = num_thread
        self.err_tolerance_num = err_tolerance_num
        self.stop_interval = stop_interval
        self.time_out_for_GET = time_out_for_GET
        self.http_headers = http_headers
        self.verbose = verbose

        self.err_cnter = 0
        self.url_cnter = 0
        self.log_file = os.path.join(out_path, 'downloaded.log')
        self._errs_cnter_lock = threading.Lock()
        self._log_lock = threading.Lock()
        # self._check_url_lock = threading.Lock()
        if not os.path.exists(out_path): 
            print('output folder is not exist, create "{}" folder'.format(out_path))
            os.makedirs(out_path)
        if not os.path.exists(self.log_file): 
            f = open(self.log_file, 'w')
            f.close()
        #self.adapter = HTTPAdapter(max_retries=3)
        #self.check_urls()
        self.update_downloading_status()

    def update_downloading_status(self):
        """ 
        The function to update the url_list, outpaht_list, and img_hash_set, based on the log in the folder.
        This is useful when you already have some urls downloaded in the output folder.
        This function depnds on the log file in the output folder.

        Parameters: 
            None  

        Returns: 
            None
        """
        tmp = []
        with open(self.log_file, 'r') as f:
            downloaded_url = collections.Counter([line.split('\t')[0] for line in f])
        for url, outpath in zip(self.url_list, self.outpath_list):
            if url in downloaded_url:
                downloaded_url[url] -= 1
                if downloaded_url[url] == 0:
                    downloaded_url.pop(url)
            else:
                tmp.append((url, outpath))
        # random.shuffle(tmp)
        self.url_list = [i[0] for i in tmp]
        self.outpath_list = [i[1] for i in tmp]

    def get_num_urls_needed(self):
        """ 
        This function returns the number of urls needs to download to the output folder.
        
        Parameters: 
            None  

        Returns: 
            len(self.url_list) (int)
        """
        self.update_downloading_status()
        return len(self.url_list)

    def get_outpath_from_url(self, url):
        """ 
        This function generate output path for an url, based on the filename in an url.
        
        Parameters: 
            url (string)  

        Returns: 
            outpath (string): output path for the given url
        """
        parsing = urlparse(url)
        if os.path.basename(parsing.path):
            fname = os.path.basename(parsing.path)
        else:
            fname = parsing.netloc
        outpath = os.path.join(self.out_path, fname)
        return outpath

    def download_site(self, url, outpath):
        """
        This function download an url and save its content to the outpath.

        Parameters:
            url (string): the url to downalod.
            outpath (string): the output path to save the content.

        Returns:
            None
        """
        session = get_session()
        session.headers.update(self.http_headers)
        #session.mount(url, self.adapter) #for retry
        #print('url: {}, outpath: {}'.format(url, outpath))
        with session.get(url, timeout=self.time_out_for_GET) as response:
            if response:
                if self.verbose:
                    print('o', end='', file=sys.stderr, flush=True)
                self.url_cnter += 1
                if self.url_cnter % 1000 == 0 and self.verbose:
                    print('# processed url: {}...'.format(self.url_cnter), end='', file=sys.stderr, flush=True)
                #print(f"Read {len(response.content)} from {url}")
                with open(outpath, 'wb') as f:
                    f.write(response.content)
                with self._log_lock:
                    with open(self.log_file, 'a') as f:
                        f.write('{}\t{}\n'.format(url, 'o'))
                with self._errs_cnter_lock:
                    self.err_cnter = 0
            else:
                print('x', end='', file=sys.stderr, flush=True)
                self.url_cnter += 1
                if self.url_cnter % 1000 == 0:
                    print('# processed url: {}...'.format(self.url_cnter), end='', file=sys.stderr, flush=True)
                with self._errs_cnter_lock:
                    if self.err_cnter >= self.err_tolerance_num:
                        time.sleep(self.stop_interval)
                        self.err_cnter = 0
                        print('last error code is {}, error url: {}'.format(response.status_code, url), file=sys.stderr, flush=True)
                    else:
                        self.err_cnter += 1
                with self._log_lock:
                    with open(self.log_file, 'a') as f:
                        f.write('{}\t{}\n'.format(url, 'x'))

    def download_all_sites(self):
        """ 
        This function calls [self.num_thread] threads to download urls.
        Its a multithread version of download_site
        
        Parameters: 
            None
        Returns: 
            None
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_thread) as executor:
            executor.map(self.download_site, self.url_list, self.outpath_list)

    def batch_download_sites(self, num):
        """ 
        This function calls [self.num_thread] threads to download urls.
        Its a multithread version of download_site
        
        Parameters: 
            None
        Returns: 
            None
        """
        print('# files to download: {}'.format(len( self.url_list[:num])))
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_thread) as executor:
            executor.map(self.download_site, self.url_list[:num], self.outpath_list[:num])
        self.update_downloading_status()


class URLDownloader_v2:
    """ 
    This is a class for downloading a batch of urls via http connection.
      
    Attributes: 
        url_list (list): a list of url to download.
        local_output_path (string): the path to the output folder /
        output_path_list (list): appending the outname with outpath.
        num_thread (int): the number of thread used for download.
        err_tolerance_num (int): the number of error tolerance for downloading.
        stop_interval (int): the secs to stop after error number exceeds the  err_tolerance_num
        timeout (int): the time limit for http GET
        http_headers (dict): the header for http.
        output_name_list (list): the list for the output file name. The default behaviour is using the file name in the url. If this is specified, it will overwrite the default name.
        err_cnter (int): counter for counting consecutive errors.
        log_file (string): a file name for logging, saving inside the out_path.
    """
    def __init__(self,
                 url_list: List,
                 local_output_path: str,
                 num_thread: int=4,
                 err_tolerance_num: int=1000,
                 stop_interval: int=0,
                 timeout: int=600,
                 http_headers: Dict={},
                 output_name_list: Optional[List]=None,
                 verbose: bool=True,
                 custom_img_saver: Optional[Callable[[str, bytes], None]]=None
                 ):
        """ 
        The constructor for URLDownloader Class. It saves the parameters as attributes, set some attributes, and call update_downloading_status
        
        Parameters: 
            url_list (list): a list of url to download.
            local_output_path (string): the path to the output folder
            num_thread (int): the number of thread used for download.
            err_tolerance_num (int): the number of error tolerance for downloading.
            stop_interval (int): the secs to stop after error number exceeds the  err_tolerance_num
            timeout (int): the time limit for http GET
            http_headers (dict): the header for http.
            output_name_list (list): the list for the output file name. The default behaviour is using the file name in the url. If this is specified, it will overwrite the default name.
                    
        Returns: 
            The URLDownloader object
        """
        self.local_output_path = local_output_path
        if output_name_list:
            assert(len(url_list) == len(output_name_list))
            self.output_path_list = [os.path.join(local_output_path, "data", name) for name in output_name_list]
        else:
            url_list = list(set(url_list))
            self.output_path_list = [self.get_outpath_from_url(i) for i in url_list]
        self.url_list = url_list

        self.num_thread = num_thread
        self.err_tolerance_num = err_tolerance_num
        self.stop_interval = stop_interval
        self.timeout = timeout
        self.http_headers = http_headers
        self.verbose = verbose
        self.custom_img_saver = custom_img_saver

        self.err_cnter = 0
        self.url_cnter = 0
        self.log_file = os.path.join(local_output_path, "downloaded.log")
        data_path = os.path.join(local_output_path, "data")
        if not os.path.exists(data_path):
            logger.info(f"Output folder is not exist, create folder: {data_path}")
            os.makedirs(data_path)
        if not os.path.exists(self.log_file):
            logger.info("Creating log_file")
            f = open(self.log_file, "w")
            f.close()
        self.update_downloading_status()

    def update_downloading_status(self):
        """ 
        The function to update the url_list, outpaht_list, and img_hash_set, based on the log in the folder.
        This is useful when you already have some urls downloaded in the output folder.
        This function depnds on the log file in the output folder.

        Parameters: 
            None  

        Returns: 
            None
        """
        tmp = []
        with open(self.log_file, "r") as f:
            downloaded_url = collections.Counter([line.split("\t")[0] for line in f if "batch above" not in line])
        for url, output_path in zip(self.url_list, self.output_path_list):
            if url in downloaded_url:
                downloaded_url[url] -= 1
                if downloaded_url[url] == 0:
                    downloaded_url.pop(url)
            else:
                tmp.append((url, output_path))

        self.url_list = [i[0] for i in tmp]
        self.output_path_list = [i[1] for i in tmp]

    def get_num_urls_needed(self) -> int:
        """ 
        This function returns the number of urls needs to download to the output folder.
        
        Parameters: 
            None  

        Returns: 
            len(self.url_list) (int)
        """
        self.update_downloading_status()
        return len(self.url_list)

    def get_outpath_from_url(self, url: str) -> str:
        """ 
        This function generate output path for an url, based on the filename in an url.
        
        Parameters: 
            url (string)  

        Returns: 
            outpath (string): output path for the given url
        """
        parsing = urlparse(url)
        if os.path.basename(parsing.path):
            fname = os.path.basename(parsing.path)
        else:
            fname = parsing.netloc
        outpath = os.path.join(self.local_output_path, "data", fname)
        return outpath

    def download_site(self, url: str, outpath: str, log_flag: bool=False) -> Tuple[List, List]:
        """
        This function download an url and save its content to the outpath.

        Parameters:
            url (string): the url to downalod.
            outpath (string): the output path to save the content.

        Returns:
            None
        """
        session = get_session()
        session.headers.update(self.http_headers)

        print_to_log_file = []
        print_to_stderr = []
        with session.get(url, timeout=self.timeout) as response:
            if response:
                if self.verbose:
                    print_to_stderr.append("o")
                self.url_cnter += 1
                if self.url_cnter % 1000 == 0 and self.verbose:
                    print_to_stderr.append("# processed url: {}...".format(self.url_cnter))

                if self.custom_img_saver:
                    self.custom_img_saver(outpath, response)
                else:
                    with open(outpath, "wb") as f:
                        f.write(response.content)
                print_to_log_file.append("{}\t{}\n".format(url, "o"))
                set_to_zero_thread_local_err_cntr()
            else:
                print_to_stderr.append("x")
                self.url_cnter += 1
                if self.url_cnter % 1000 == 0:
                    print_to_stderr.append("# processed url: {}...".format(self.url_cnter))
                if get_thread_local_err_cntr() >= self.err_tolerance_num:
                    time.sleep(self.stop_interval)
                    set_to_zero_thread_local_err_cntr()
                    print_to_stderr.append("last error code is {}, error url: {}".format(response.status_code, url))
                increment_thread_local_err_cntr()
                print_to_log_file.append("{}\t{}\n".format(url, "x"))
        if log_flag:
            for to_print in print_to_stderr:
                print(to_print, end="", file=sys.stderr)
            print("\n", end="", file=sys.stderr, flush=True)

            with open(self.log_file, "a") as f:
                for to_print in print_to_log_file:
                    f.write(to_print)
       
        return (print_to_log_file, print_to_stderr)

    def batch_download_sites(self, batch_size: int=-1):
        """ 
        This function calls [self.num_thread] threads to download urls.
        Its a multithread version of download_site. It download first [batch_size] of images
        
        Parameters: 
            batch_size (int): the size of image to be downloaded from url_list.
        Returns: 
            None
        """
        if batch_size == -1:
            num = len(self.url_list)
        print("# files to download: {}".format(len( self.url_list[:num])))
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_thread) as executor:
            results = executor.map(self.download_site, self.url_list[:num], self.output_path_list[:num])

        results = list(results)
        for _, print_to_stderr in results:
            for to_print in print_to_stderr:
                print(to_print, end="", file=sys.stderr)
        print("\n", end="", file=sys.stderr, flush=True)

        with open(self.log_file, "a") as f:
            for print_to_log_file, _ in results:
                for to_print in print_to_log_file:
                    f.write(to_print)

        self.update_downloading_status()


    def download_all_sites(self, batch_size: int=1024):
        """ 
        This function is a wrapper for batch_download_sites, it downloads all images in a batch way.
        
        Parameters: 
            batch_size (int): the size of image to be download and output logs.
        Returns: 
            None
        """
        while len(self.url_list) > 0:
            self.batch_download_sites(batch_size)


if __name__ == "__main__":
    import shutil
    if os.path.exists('test_out'):
        shutil.rmtree('test_out')
    sites = [
        "https://www.jython.org/",
        "http://olympus.realpython.org/dice",
        'https://mobileimages.lowes.com/product/converted/885612/885612278951.jpg?size=xl',
        'https://images.lowes.com/product/converted/885612/885612277671lg.jpg',
        'https://images.lowes.com/product/converted/885612/885612279095lg.jpg'
    ] 
    print('testing constructor...')
    downloader = URLDownloader_v2(sites, 'test_out', 3, output_name_list=['1', '2', '3', '4', '5'])
    print('the urls need to be downloaded:')
    print(downloader.url_list)
    print('the output path to save files:')
    print(downloader.output_path_list)
    print('--------------------------------------------------------------------')
    input('Press "Enter" to continue...')

    print('testing download_site...')
    downloader.download_site(downloader.url_list[0], downloader.output_path_list[0], True)
    downloader.download_site(downloader.url_list[1], downloader.output_path_list[1], True)
    print('update_downloading_status...')
    downloader.update_downloading_status()
    print('the urls need to be downloaded:')
    print(downloader.url_list)
    print('the output path to save files:')
    print(downloader.output_path_list)    
    print('--------------------------------------------------------------------')
    input('Press "Enter" to continue...')

    print('testing download_all_sites..')
    downloader.batch_download_sites()
    print('update_downloading_status...')
    downloader.update_downloading_status()
    print('the urls need to be downloaded:')
    print(downloader.url_list)
    print('the output path to save files:')
    print(downloader.output_path_list)
    print('--------------------------------------------------------------------')
    input('Press "Enter" to continue...')

    print('testing error urls...')
    sites = [s + 'abcde' for s in sites]
    print(sites)
    if os.path.exists('test_out2'):
        shutil.rmtree('test_out2')
    downloader = URLDownloader_v2(sites, 'test_out2', 3, err_tolerance_num=10, stop_interval=5)
    downloader.batch_download_sites()
    print('--------------------------------------------------------------------')
    input('Press "Enter" to continue...')
