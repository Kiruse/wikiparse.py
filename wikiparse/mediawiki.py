"""Central configuration for a specific MediaWiki project."""
from __future__ import annotations
import asyncio
from typing import *
from iso639 import Lang
from piodispatch import ascoroutine

from .ast import AST, ASTList
from .error import APIError
from .interface.requester import Requester
from .interface.logger import Logger
from .parser import parsepage
from .renderer import HTMLRenderer, Renderer
from .transformer import Transcluder, Variables
from .transformer.transcluder import TranscluderAPI
from .utils import first
from .wikipage import WikiNamespace, WikiPage
import requests

rget = ascoroutine(requests.get)

class MediaWiki:
  """`MediaWiki` is your interface to an arbitrary WikiMedia style wiki website.
  
  The constructor supports different, optional keyword arguments:
  * `language: str` to use, using ISO-639 language codes. Defaults to `'en'`.
  * `requester: Requester` web request limiter. Optional.
  * `logger: Logger` instance to use. Used to log additional verbose & debugging information. Optional.
  * `namespaces: Dict[str | int, WikiNamespace]` mapping of namespace IDs and/ornames to `WikiNamespace` instances.
    Optional. Should be populated at runtime using `await wiki.query_namespaces()`.
  * `renderer: Renderer` to use for rendering AST to string. Defaults to `HTMLRenderer()`.
  * `transcluder: Transcluder` to use for transcluding templates & co into callsites. Optional.
  * `templates: Dict[str, WikiPage]` mapping of predefined templates to their respective AST. Allows overriding. Optional.
  """
  def __init__(self, host = 'wikipedia.org', **kwargs):
    self.host = host
    self.language: Lang = Lang(kwargs.pop('language', 'en')) # raises if language is invalid
    self.requester: Requester | None = kwargs.pop('requester', None)
    self.logger: Logger | None = kwargs.pop('logger', None)
    self.namespaces: Dict[str | int, WikiNamespace] = {}
    self.renderer: Renderer = kwargs.pop('renderer', HTMLRenderer())
    self.transcluder = Transcluder(kwargs.pop('transcluder_api', MediaWikiTranscluderAPI(self)), self.logger)
    self.templates: Dict[str, WikiPage] = kwargs.pop('templates', dict())
  
  @property
  def baseurl(self) -> str:
    return f'https://{self.language.pt1}.{self.host}'
  
  async def query_namespaces(self):
    """Query the namespaces of this MediaWiki project."""
    get = self.requester.get if self.requester else rget
    
    params = {
      'action': 'query',
      'meta': 'siteinfo',
      'siprop': 'namespaces|namespacealiases',
      'format': 'json',
    }
    
    res = await get(f'{self.baseurl}/w/api.php', params=params)
    json = res.json()
    
    if 'error' in json:
      raise APIError(json['error']['info'])
    assert 'batchcomplete' in json
    
    for ns in json['query']['namespaces'].values():
      inst = WikiNamespace(ns['*'], ns['canonical'] if 'canonical' in ns else None, [], ns['id'])
      self.namespaces[inst.id] = inst
      self.namespaces[inst.name] = inst
      if inst.canonical:
        self.namespaces[inst.canonical] = inst
    for alias in json['query']['namespacealiases']:
      inst = self.namespaces[alias['id']]
      inst.aliases.append(alias['*'])
      self.namespaces[alias['*']] = inst
    
    return self
  
  async def fetch_page(self, title: str, *, namespace: str = '') -> WikiPage:
    "Fetch the given page's parsed WikiText as an AST."
    file = f'{namespace}:{title}' if namespace else title
    if self.logger:
      self.logger.v(f'Fetching page {file}')
    page = await self.get_revision(file)
    page.parse(logger=self.logger)
    return page
  
  async def fetch_template(self, name: str) -> WikiPage:
    if name not in self.templates:
      self.templates[name] = await self.fetch_page(name, namespace='Template')
    elif self.logger:
      self.logger.d(f'Template {name} was cached')
    return self.templates[name]
  
  async def fetch_template_ast(self, name: str) -> ASTList:
    return (await self.fetch_template(name)).parse(logger=self.logger)
  
  async def fetch_module(self, name: str) -> str:
    "Fetching a Module differs from fetching a regular page in that it returns the raw LUA source code as a string."
    raise NotImplementedError()
  
  async def transclude(self, ast: ASTList | WikiPage, vars: Variables | None = None, page: WikiPage | None = None) -> ASTList:
    if vars is None: vars = dict()
    if hasattr(ast, 'parse'):
      page = ast
      _, ast = page.parse(logger=self.logger)
    # TODO: first transform ast by filtering out <onlyinclude> and <includeonly> nodes
    return await self.transcluder.transform(ast, vars, page=page)
  
  def render(self, ast: ASTList) -> str:
    return self.renderer.render(ast)
  
  async def get_revision(self, title: str, *args, **kwargs) -> WikiPage:
    """Shortcut for `MediaWiki.get_revisions_for((title,), *args, **kwargs)`.
    Thus accepts the same positional and keyword arguments as `MediaWiki.get_revisions_for`."""
    return await self.get_revisions_for((title,), *args, **kwargs)
  
  async def get_revisions_for(self, titles: Sequence[str]) -> Dict[str, WikiPage] | WikiPage:
    """Retrieve the latest revision for each page listed by `titles`.
    Return a single revision if only one title is given, otherwise a mapping from title to revision.
    """
    get = self.requester.get if self.requester else rget
    
    params = {
      'action': 'query',
      'titles': '|'.join(titles),
      'prop': 'revisions',
      'rvprop': 'content',
      'rvslots': 'main',
      'format': 'json',
    }
    
    res = await get(f'{self.baseurl}/w/api.php', params=params)
    json = res.json()
    
    if 'error' in json:
      raise APIError(json['error']['info'])
    
    pages = json['query']['pages'].values()
    if len(titles) == 1:
      return await self._get_revision_from(first(pages))
    else:
      # map pages/titles to list of revisions
      return dict(
        zip(
          (page['title'] for page in pages),
          await asyncio.gather(*(
            self._get_revision_from(page)
            for page in pages
          ))
        )
      )
  
  async def _get_revision_from(self, data: Dict):
    if 'revisions' not in data:
      raise FileNotFoundError(f'page "{self.baseurl}/wiki/{data["title"]}" not found')
    
    rev = data['revisions'][0]['slots']['main']
    assert rev['contentmodel'] == 'wikitext'
    assert rev['contentformat'] == 'text/x-wiki'
    return WikiPage(
      data['title'],
      rev['*'],
      rev['contentformat'],
      self.namespaces[data['ns']] if data['ns'] in self.namespaces else DEFAULT_NS
    )

class MediaWikiTranscluderAPI(TranscluderAPI):
  def __init__(self, wiki: MediaWiki):
    self.wiki = wiki
  
  async def fetch_template(self, name: str) -> WikiPage:
    return await self.wiki.fetch_template(name)
  
  async def page_exists(self, page: str) -> bool:
    try:
      await self.fetch_template(self, page)
      return True
    except FileNotFoundError:
      return False
  
  async def invoke(self, mod: str, fn: str, vars: Variables) -> str:
    """Invoke a LUA module - however, WikiParse currently does not support interpreting LUA and thus requires a custom
    implementation of this method."""
    raise NotImplementedError()
  
  def render(self, ast: ASTList) -> str:
    return self.wiki.renderer.render(ast)
  
  def renderid(self, ast: ASTList) -> str:
    return self.render(ast)

DEFAULT_NS = WikiNamespace('', None, [], 0)
