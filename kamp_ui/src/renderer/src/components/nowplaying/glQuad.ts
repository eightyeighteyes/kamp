// KAMP-776 spike: the whole "WebGL framework" a fullscreen shader background
// needs — compile/link one program, a single fullscreen triangle, and a cached
// uniform lookup. Everything here dies with the context, so after
// webglcontextrestored the caller simply builds a fresh one.

const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`

export interface GlQuad {
  program: WebGLProgram
  /** Cached uniform location; null if the shader optimised the uniform away. */
  u(name: string): WebGLUniformLocation | null
  draw(): void
  dispose(): void
}

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type)
  if (!sh) return null
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.warn('[KAMP-776] shader compile failed:', gl.getShaderInfoLog(sh))
    gl.deleteShader(sh)
    return null
  }
  return sh
}

/** Returns null on any failure (compile/link/allocation) so the caller can fall back. */
export function createQuad(gl: WebGLRenderingContext, fragSrc: string): GlQuad | null {
  const vs = compile(gl, gl.VERTEX_SHADER, VERT)
  const fs = compile(gl, gl.FRAGMENT_SHADER, fragSrc)
  const program = gl.createProgram()
  if (!vs || !fs || !program) return null
  gl.attachShader(program, vs)
  gl.attachShader(program, fs)
  gl.bindAttribLocation(program, 0, 'aPos')
  gl.linkProgram(program)
  // Shaders can be flagged for deletion once linked; the program keeps them alive.
  gl.deleteShader(vs)
  gl.deleteShader(fs)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn('[KAMP-776] program link failed:', gl.getProgramInfoLog(program))
    gl.deleteProgram(program)
    return null
  }

  // One oversized triangle covers the viewport with no diagonal seam (vs two-tri quad).
  const buf = gl.createBuffer()
  if (!buf) return null
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)

  const locs = new Map<string, WebGLUniformLocation | null>()
  return {
    program,
    u(name) {
      if (!locs.has(name)) locs.set(name, gl.getUniformLocation(program, name))
      return locs.get(name) ?? null
    },
    draw() {
      gl.useProgram(program)
      gl.bindBuffer(gl.ARRAY_BUFFER, buf)
      gl.enableVertexAttribArray(0)
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    },
    dispose() {
      gl.deleteBuffer(buf)
      gl.deleteProgram(program)
    }
  }
}
