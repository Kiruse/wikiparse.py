from wikiparse.renderer.html import HTMLRenderer
from wikiparse.transformer.transformer import Variables
from ..ast import *
from ..parser import parse
from .transcluder import Transcluder, TranscluderAPI
import pytest

class API(TranscluderAPI):
  def __init__(self):
    self.renderer = HTMLRenderer()
  
  async def fetch_template(self, name: str) -> ASTList:
    if name == 'foo':
      return [TextNode('foo')]
    if name == 'nested':
      return [TemplateNode('foo', [], [])]
    if name == 'with-var':
      return [VariableNode('1', None)]
  
  async def page_exists(self, name: str) -> bool:
    return name in ('foo', 'nested', 'with-var')
  
  async def invoke(self, mod: str, fn: str, vars: Variables) -> str:
    if mod == 'foo':
      if fn == 'bar':
        return 'baz'
    if mod == 'bar':
      if fn == 'foo':
        return self.render(vars['foo'])
      if fn == 'baz':
        return self.render(vars['baz'])
    return ''
  
  def render(self, ast: ASTList) -> str:
    return self.renderer.render(ast)
  
  def renderid(self, ast: ASTList) -> str:
    return self.render(ast)

@pytest.mark.asyncio
async def test_identity():
  tf = Transcluder(API())
  ast = [TextNode('foo'), TextNode('bar')]
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == ast

@pytest.mark.asyncio
async def test_simple_template():
  tf = Transcluder(API())
  ast = parse('foo{{foo}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('foo'), TextNode('foo')]

@pytest.mark.asyncio
async def test_template_with_var():
  tf = Transcluder(API())
  ast = parse(r'{{with-var|foo}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('foo')]

@pytest.mark.asyncio
async def test_nested_template():
  tf = Transcluder(API())
  ast = parse(r'{{nested}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('foo')]

@pytest.mark.asyncio
async def test_evaluate_if():
  tf = Transcluder(API())
  ast = parse(r'{{#if:foo|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('true')]
  
  ast = parse(r'{{#if:|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('false')]
  
  ast = parse(r'{{#if: |true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('false')]

@pytest.mark.asyncio
async def test_evaluate_ifeq():
  tf = Transcluder(API())
  ast = parse(r'{{#ifeq:lhs|rhs|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('false')]
  
  ast = parse(r'{{#ifeq:val|val|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('true')]
  
  ast = parse(r'{{#ifeq:val | val|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('true')]
  
  ast = parse(r'{{#ifeq:|val|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('false')]

@pytest.mark.asyncio
async def test_evaluate_ifexist():
  tf = Transcluder(API())
  ast = parse(r'{{#ifexist:foo|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('true')]
  
  ast = parse(r'{{#ifexist:nonexistent|true|false}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == [TextNode('false')]

@pytest.mark.asyncio
async def test_invoke():
  tf = Transcluder(API())
  ast = parse(r'{{#invoke:foo|bar}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == ['baz']
  
  ast = parse(r'{{#invoke:bar|foo|foo=42}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == ['42']
  
  ast = parse(r'{{#invoke:bar|baz|baz=43}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == ['43']
  
  ast = parse(r'{{#invoke:foo|boz}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == ['']
  
  ast = parse(r'{{#invoke:bonk|blorgh}}')
  assert await tf.matches(ast)
  assert await tf.transform(ast, dict()) == ['']
