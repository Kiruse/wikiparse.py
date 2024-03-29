from __future__ import annotations
from typing import *
from ..ast import *
from ..interface import Logger
from ..renderer.identifier import IdentifierRenderer
from ..utils import isiterable, iterable
from ..wikipage import WikiPage
from .transformer import Transformer, Variables, make_vars
from .inclusion import InclusionTransformer

identifier_renderer = IdentifierRenderer()
inclusion_transformer = InclusionTransformer()

class TranscluderAPI:
  async def fetch_template(self, name: str) -> WikiPage:
    raise NotImplementedError()
  
  async def page_exists(self, page: str) -> bool:
    raise NotImplementedError()
  
  async def invoke(self, mod: str, fn: str, vars: Variables) -> str:
    raise NotImplementedError()
  
  def render(self, ast: ASTList) -> str:
    raise NotImplementedError()
  
  def renderid(self, ast: ASTList) -> str:
    raise NotImplementedError()

class Transcluder(Transformer):
  def __init__(self, api: TranscluderAPI, logger: Logger | None = None):
    self.api = api
    self.logger = logger
  
  async def transform(self, ast: ASTList, vars: Variables, page: WikiPage | None = None) -> ASTList:
    if not vars:
      vars = dict()
    
    if isiterable(ast) and type(ast) is not str:
      result = []
      for node in ast:
        transcluded = await self.transform(node, vars, page)
        if type(transcluded) is unit:
          result.extend(transcluded.ast)
        else:
          result.append(transcluded)
      return result
    
    elif AST.isastlike(ast):
      fn = f'_transclude_{ast.name}'
      if hasattr(self, fn):
        return await getattr(self, fn)(ast, vars, page)
      else:
        ast.children = await self.transform(ast.children, vars, page)
        return ast
    
    else:
      return ast
  
  async def _transclude_template(self, tpl: TemplateNode, vars: Variables, page: WikiPage | None):
    name, posargs, namedargs = await self.transform(tpl.children, vars, page)
    name = self.api.renderid(name)
    if self.logger:
      if page:
        self.logger.d(f'Transcluding {name} into {page.title}')
      else:
        self.logger.d(f'Transcluding {name} into unknown page')
    
    tplpage = await self.api.fetch_template(name)
    _, tplast = tplpage.parse(logger=self.logger)
    vars = self.make_vars(posargs, namedargs)
    return unit(iterable(await transclude_inclusion(await self.transform(tplast, vars, tplpage), page)))
  
  async def _transclude_variable(self, var: VariableNode, vars: Variables, page: WikiPage | None):
    name, default = var.children
    name = self.api.renderid(name)
    return unit(iterable(vars[name] if name in vars else default))
  
  async def _transclude_if(self, node: IfNode, vars: Variables, page: WikiPage | None):
    cond, true, false = await self.transform(node.children, vars, page)
    if self.api.render(cond).strip():
      return unit(true)
    else:
      return unit(false)
  
  async def _transclude_ifeq(self, node: IfEqNode, vars: Variables, page: WikiPage | None):
    lhs, rhs, true, false = await self.transform(node.children, vars, page)
    slhs = self.api.render(lhs)
    srhs = self.api.render(rhs)
    if slhs.strip() == srhs.strip():
      return unit(true)
    else:
      return unit(false)
  
  async def _transclude_ifexist(self, node: IfExistNode, vars: Variables, page: WikiPage | None):
    file, true, false = await self.transform(node.children, vars, page)
    if await self.api.page_exists(self.api.render(file)):
      return unit(true)
    else:
      return unit(false)
  
  async def _transclude_switch(self, node: SwitchNode, vars: Variables, page: WikiPage | None):
    val, branches = await self.transform(node.children, vars, page)
    val = self.api.render(val).strip()
    branches = self.make_switch_map(branches)
    
    if val in branches:
      return unit(branches[val])
    if '#default' in branches:
      return unit(branches['#default'])
    return unit([])
  
  async def _transclude_invoke(self, node: InvokeNode, vars: Variables, page: WikiPage | None):
    mod, fn, posargs, namedargs = await self.transform(node.children, vars, page)
    mod = self.api.renderid(mod).strip()
    fn  = self.api.renderid(fn).strip()
    vars = self.make_vars(posargs, namedargs)
    return await self.api.invoke(mod, fn, vars)
  
  def make_vars(self, posargs: Sequence[PosArgNode], namedargs: Sequence[NamedArgNode]) -> Variables:
    return make_vars(self.api.render, posargs, namedargs)
  
  def make_switch_map(self, branches: Sequence[SwitchBranchNode]):
    res: Dict[str, ASTList] = dict()
    for branch in branches:
      cmp, val = branch.children
      res[self.api.render(cmp).strip()] = val
    return res

class unit:
  """Simple wrapper around a `List[AST]` with the semantics that
  interpreting code should flatten instances into an encapsulating list."""
  def __init__(self, ast: List[AST]):
    self.ast = ast

async def transclude_inclusion(ast: ASTList, page: WikiPage) -> ASTList:
  return await inclusion_transformer.transform(ast, dict(), page)

FetchTemplate = Callable[[str], Awaitable[ASTList]]
