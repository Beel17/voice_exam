// Runs on the browser's audio thread: hands blocks of microphone samples to the page, but
// only while the talk key is held (the page sends "on" and "off"). Passing every block
// all the time would wake the page several hundred times a second for the whole exam,
// which costs battery for nothing.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.on = false;
    this.port.onmessage = ({ data }) => {
      this.on = data === "on";
    };
  }

  process(inputs) {
    const channel = this.on && inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor("capture", Capture);
