export class HPGLViewer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;

  private color: string = "white"; // Default color for paths
  private scale: number = 1;
  private offsetX: number = 0;
  private offsetY: number = 0;

  private paths: {
    pathCount: number;
    totalPointCount: number;
    colorCode: number[];
    pointCounts: number[];
    x: number[];
    y: number[];
    feedLength: number[];
  };

  private origin: {
    pathCount: number;
    totalPointCount: number;
    colorCode: number[];
    pointCounts: number[];
    x: number[];
    y: number[];
  };

  private feedLengths: number[] = [];

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;

    this.color = "white"; // Default color for paths
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;

    this.paths = {
      pathCount: 0,
      totalPointCount: 0,
      colorCode: [],
      pointCounts: [],
      x: [],
      y: [],
      feedLength: [],
    };

    this.origin = {
      pathCount: 0,
      totalPointCount: 0,
      colorCode: [],
      pointCounts: [],
      x: [],
      y: [],
    };

    this.feedLengths = [];

    this.getOrigin();

    this.canvas.height = window.innerHeight - 55;
    this.canvas.width = window.innerWidth - 55;

    // Mouse double click event listener.
    this.canvas.ondblclick = () => {
      this.fitToScreen();
    };
  }

  public loadHPGL(file: string): void {
    this.getPaths(file);
    this.fitToScreen();
  }

  private getOrigin() {
    this.origin.x[0] = 0;
    this.origin.y[0] = 10000;
    this.origin.x[1] = 0;
    this.origin.y[1] = 0;
    this.origin.x[2] = 10000;
    this.origin.y[2] = 0;
    this.origin.colorCode[0] = 0;
    this.origin.pointCounts[0] = 3;

    this.origin.x[3] = -500;
    this.origin.y[3] = 9000;
    this.origin.x[4] = 0;
    this.origin.y[4] = 10000;
    this.origin.x[5] = +500;
    this.origin.y[5] = 9000;
    this.origin.colorCode[1] = 0;
    this.origin.pointCounts[1] = 3;

    this.origin.x[6] = 9000;
    this.origin.y[6] = 500;
    this.origin.x[7] = 10000;
    this.origin.y[7] = 0;
    this.origin.x[8] = 9000;
    this.origin.y[8] = -500;
    this.origin.colorCode[2] = 0;
    this.origin.pointCounts[2] = 3;

    this.origin.x[9] = -1000;
    this.origin.y[9] = 10000;
    this.origin.x[10] = -1500;
    this.origin.y[10] = 9200;
    this.origin.x[11] = -2000;
    this.origin.y[11] = 10000;
    this.origin.colorCode[3] = 0;
    this.origin.pointCounts[3] = 3;

    this.origin.x[12] = -1500;
    this.origin.y[12] = 9200;
    this.origin.x[13] = -1500;
    this.origin.y[13] = 8000;
    this.origin.colorCode[4] = 0;
    this.origin.pointCounts[4] = 2;

    this.origin.x[14] = 10000;
    this.origin.y[14] = -1000;
    this.origin.x[15] = 9000;
    this.origin.y[15] = -3000;
    this.origin.colorCode[5] = 0;
    this.origin.pointCounts[5] = 2;

    this.origin.x[16] = 9000;
    this.origin.y[16] = -1000;
    this.origin.x[17] = 10000;
    this.origin.y[17] = -3000;
    this.origin.colorCode[6] = 0;
    this.origin.pointCounts[6] = 2;

    this.origin.totalPointCount = 18;
    this.origin.pathCount = 7;
  }

  // CAD utilities.
  private getPaths(contents: string): void {
    const lines = contents.split(";");
    let currentColorCode = 0;
    let feedLength = 0;
    let feedCount = 0;
    this.paths.totalPointCount = 0;
    this.paths.pathCount = 0;

    console.log(`HPGL: Parsing ${lines.length} lines...`);

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const parms = line.split(/[PUDNSFL,;]/);
      // console.log(`HPGL: Parsing line ${i + 1}: ${line}, ${parms}`);

      if (line[0] === "S" && line[1] === "P") {
        currentColorCode = parms[2] ? parseInt(parms[2]) : 0;
      } else if (line[0] === "P" && line[1] === "U" && line[2] != ";") {
        const x = parseInt(parms[2]);
        const y = parseInt(parms[3]);

        this.paths.pathCount++;
        this.paths.x[this.paths.totalPointCount] = x;
        this.paths.y[this.paths.totalPointCount] = y;
        this.paths.feedLength[this.paths.totalPointCount++] = feedLength;
        this.paths.pointCounts[this.paths.pathCount - 1] = 1;
        this.paths.colorCode[this.paths.pathCount - 1] = currentColorCode;
      } else if (line[0] === "P" && line[1] === "D" && line[2] != ";") {
        const x = parseInt(parms[2]);
        const y = parseInt(parms[3]);

        this.paths.x[this.paths.totalPointCount] = x;
        this.paths.y[this.paths.totalPointCount] = y;
        this.paths.feedLength[this.paths.totalPointCount++] = feedLength;
        this.paths.pointCounts[this.paths.pathCount - 1]++;
      } else if (line[0] === "F" && line[1] === "L" && line[2] != ";") {
        feedLength += parseInt(parms[2]);
        this.feedLengths[feedCount] = feedLength;
        feedCount++;
      }
    }

    console.log(
      `HPGL: ${this.paths.pathCount} paths, ${this.paths.totalPointCount} points, ${feedCount} feed lengths`
    );
  }

  private fitToScreen() {
    let minX = this.paths.x[0];
    let minY = this.paths.y[0];
    let maxX = minX;
    let maxY = minY;

    for (let i = 1; i < this.paths.totalPointCount; i++) {
      if (this.paths.x[i] < minX) {
        minX = this.paths.x[i];
      } else if (this.paths.x[i] > maxX) {
        maxX = this.paths.x[i];
      }

      if (this.paths.y[i] < minY) {
        minY = this.paths.y[i];
      } else if (this.paths.y[i] > maxY) {
        maxY = this.paths.y[i];
      }
    }

    for (let i = 1; i < this.origin.totalPointCount; i++) {
      if (this.origin.x[i] < minX) {
        minX = this.origin.x[i];
      } else if (this.origin.x[i] > maxX) {
        maxX = this.origin.x[i];
      }

      if (this.origin.y[i] < minY) {
        minY = this.origin.y[i];
      } else if (this.origin.y[i] > maxY) {
        maxY = this.origin.y[i];
      }
    }

    const dx = maxX - minX;
    const dy = maxY - minY;

    const sx = dx / (this.canvas.width - 100);
    const sy = dy / (this.canvas.height - 100);

    if (sx > sy) {
      this.scale = sx;
      this.offsetX = -minX / this.scale + 50;
      this.offsetY =
        this.canvas.height / 2 + dy / (2 * this.scale) - maxY / this.scale;
    } else {
      this.scale = sy;
      this.offsetX =
        this.canvas.width / 2 - dx / (2 * this.scale) - minX / this.scale;
      this.offsetY = -minY / this.scale + 50;
    }

    this.draw();
  }

  private draw() {
    // Clear drawing area.
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Draw origin.
    this.ctx.strokeStyle = this.color;
    this.ctx.lineWidth = 2;
    this.ctx.lineCap = "round";
    let index = 0;

    for (let p = 0; p < this.origin.pathCount; p++) {
      this.ctx.beginPath();
      this.ctx.strokeStyle = this.selectColor(this.origin.colorCode[p]);
      let px = this.origin.x[index] / this.scale + this.offsetX;
      let py = this.origin.y[index++] / this.scale + this.offsetY;
      py = this.canvas.height - py;
      this.ctx.moveTo(px, py);

      for (let k = 1; k < this.origin.pointCounts[p]; k++) {
        px = this.origin.x[index] / this.scale + this.offsetX;
        py = this.origin.y[index++] / this.scale + this.offsetY;
        py = this.canvas.height - py;
        this.ctx.lineTo(px, py);
      }

      this.ctx.stroke();
    }

    // Draw paths.
    index = 0;

    for (let p = 0; p < this.paths.pathCount; p++) {
      this.ctx.beginPath();
      this.ctx.strokeStyle = this.selectColor(this.paths.colorCode[p]);
      let x = this.paths.x[index] + this.paths.feedLength[index];
      let px = x / this.scale + this.offsetX;
      let py = this.paths.y[index++] / this.scale + this.offsetY;
      py = this.canvas.height - py;
      this.ctx.moveTo(px, py);

      for (let k = 1; k < this.paths.pointCounts[p]; k++) {
        x = this.paths.x[index] + this.paths.feedLength[index];
        px = x / this.scale + this.offsetX;
        py = this.paths.y[index++] / this.scale + this.offsetY;
        py = this.canvas.height - py;
        this.ctx.lineTo(px, py);
      }

      this.ctx.stroke();
    }

    //Draw feeding lines.
    let maxY = this.paths.y[0];
    let minY = this.paths.y[0];
    let margin = 20000;

    for (let i = 1; i < this.paths.totalPointCount; i++) {
      if (this.paths.y[i] < minY) {
        minY = this.paths.y[i];
      }

      if (this.paths.y[i] > maxY) {
        maxY = this.paths.y[i];
      }
    }

    minY -= margin;
    maxY += margin;

    this.ctx.setLineDash([7, 5]);
    this.ctx.strokeStyle = "deepskyblue";

    for (let i = 0; i < this.feedLengths.length; i++) {
      this.ctx.beginPath();

      let px = this.feedLengths[i] / this.scale + this.offsetX;
      let py = minY / this.scale + this.offsetY;
      py = this.canvas.height - py;
      this.ctx.moveTo(px, py);

      py = maxY / this.scale + this.offsetY;
      py = this.canvas.height - py;
      this.ctx.lineTo(px, py);

      this.ctx.stroke();
    }

    this.ctx.setLineDash([0, 0]);
  }

  private selectColor(colorCode: number): string {
    if (colorCode > 10) {
      colorCode = 0;
    }

    const colorString = [
      "white",
      "lightgreen",
      "purple",
      "yellow",
      "skyblue",
      "deepskyblue",
      "blue",
      "orange",
      "salmon",
      "red",
      "tan",
    ];

    return colorString[colorCode];
  }
}
